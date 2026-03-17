"""
Streamlit UI for the A2A Orchestrator Client.

Supports both Streaming (SSE) and Non-Streaming modes.
Run with: uv run streamlit run streamlit_client.py
"""

import asyncio
import base64
import json
import uuid
from contextlib import aclosing

import httpx
import streamlit as st
from a2a.client import A2ACardResolver, Client, ClientConfig, ClientFactory
from a2a.types import (
    AgentCard,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils import get_data_parts, get_message_text

# ── Import shared message helpers from a2a_client ─────────────────────────────
from a2a_client import extract_text_from_message, make_user_message

# ── Config ────────────────────────────────────────────────────────────────────
HOST = "localhost"
PORT = 8001
BASE_URL = f"http://{HOST}:{PORT}"
AUTH_TOKEN = "Bearer ABCD"


# ── Session state init ────────────────────────────────────────────────────────

def init_session_state() -> None:
    """Initialise all session state keys on first load."""
    defaults = {
        "messages": [],          # list of {"role": "user"|"agent", "content": str}
        "artifacts": [],         # list of artifact dicts received
        "session_id": "streamlit-session-" + uuid.uuid4().hex[:8],
        "task_id": None,         # None = start new task; str = continue existing task
        "streaming": True,       # toggle between SSE streaming and non-streaming
        "agent_card": None,      # cached once per session
        "status": "",            # last task status label for display
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


# ── A2A client factory ─────────────────────────────────────────────────────────

async def _get_agent_card(httpx_client: httpx.AsyncClient) -> AgentCard:
    """Resolve agent card once; cache in session state."""
    if st.session_state.agent_card is None:
        resolver = A2ACardResolver(base_url=BASE_URL, httpx_client=httpx_client)
        card = await resolver.get_agent_card()
        assert card is not None, f"Could not resolve agent card from {BASE_URL}"
        st.session_state.agent_card = card
    return st.session_state.agent_card


def _make_httpx_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=120, headers={"token_key": AUTH_TOKEN})


# ── Non-streaming call ─────────────────────────────────────────────────────────

async def _send_non_streaming(user_text: str, data: dict | None, files: list[dict] | None) -> Task:
    """Send a message and await the final Task in one blocking HTTP call."""
    async with _make_httpx_client() as httpx_client:
        card = await _get_agent_card(httpx_client)
        client: Client = ClientFactory(
            ClientConfig(streaming=False, httpx_client=httpx_client)
        ).create(card)

        msg = make_user_message(
            text=user_text,
            context_id=st.session_state.session_id,
            task_id=st.session_state.task_id,
            data=data,
            files=files,
        )

        reply = await anext(client.send_message(msg))
        assert isinstance(reply, tuple), "Expected (Task, None) in non-streaming mode"
        task: Task = reply[0]

        if task.status.state == TaskState.completed:
            _process_completed_task(task)

        return task


def send_non_streaming(user_text: str, data: dict | None, files: list[dict] | None) -> Task:
    return asyncio.run(_send_non_streaming(user_text, data, files))


# ── Streaming call ─────────────────────────────────────────────────────────────

async def _send_streaming(
    user_text: str, 
    container, 
    data: dict | None, 
    files: list[dict] | None
) -> tuple[list[str], str, str | None]:
    """Stream SSE events and log each status update into a Streamlit st.status() block."""
    async with _make_httpx_client() as httpx_client:
        card = await _get_agent_card(httpx_client)
        client: Client = ClientFactory(
            ClientConfig(streaming=True, httpx_client=httpx_client)
        ).create(card)

        msg = make_user_message(
            text=user_text,
            context_id=st.session_state.session_id,
            task_id=st.session_state.task_id,
            data=data,
            files=files,
        )

        # State → (icon, label, expander_state)
        STATE_META = {
            TaskState.submitted:      ("📨", "Submitted",       "running"),
            TaskState.working:        ("⚙️", "Working",         "running"),
            TaskState.input_required: ("⏸️", "Input Required",  "running"),
            TaskState.completed:      ("✅", "Completed",       "complete"),
            TaskState.rejected:       ("❌", "Rejected",        "error"),
        }

        status_steps: list[str] = []
        final_agent_text: str = ""
        final_task_id: str | None = None

        # st.status() is a collapsible expander that starts expanded while running
        with container.status("🔄 Connecting to agent…", expanded=True) as status_box:
            async with aclosing(client.send_message(msg)) as stream:
                async for item in stream:
                    if not isinstance(item, tuple):
                        continue

                    task: Task = item[0]
                    final_task_id = task.id
                    st.session_state.task_id = task.id
                    task_status: TaskStatus = task.status
                    state = task_status.state

                    icon, label, _ = STATE_META.get(state, ("❓", state.value, "running"))
                    agent_text = (
                        get_message_text(task_status.message)
                        if task_status.message else ""
                    )

                    # Write one row per status update inside the expander
                    step_str = f"{icon} **{label}** — {agent_text}" if agent_text else f"{icon} **{label}**"
                    if not status_steps or status_steps[-1] != step_str:
                        status_box.write(step_str)
                        status_steps.append(step_str)

                    match state:

                        case TaskState.submitted | TaskState.working:
                            status_box.update(
                                label=f"{icon} {label}…", state="running"
                            )

                        case TaskState.input_required:
                            if not task_status.message:
                                raise ValueError("input_required must carry a message")
                            prompt = get_message_text(task_status.message)
                            st.session_state.pending_prompt = prompt
                            status_box.update(
                                label="⏸️ Waiting for your input…", state="running",
                                expanded=True,  # keep open so user sees the prompt
                            )
                            return status_steps, prompt, final_task_id

                        case TaskState.completed:
                            if agent_text:
                                final_agent_text = agent_text
                            _process_completed_task(task)
                            st.session_state.task_id = None
                            status_box.update(
                                label="✅ Task completed", state="complete",
                                expanded=False,
                            )
                            return status_steps, final_agent_text, final_task_id

                        case TaskState.rejected:
                            status_box.update(
                                label="❌ Task rejected", state="error", expanded=True
                            )
                            st.session_state.task_id = None
                            if task_status.message:
                                final_agent_text = get_message_text(task_status.message)
                            return status_steps, final_agent_text, final_task_id

                        case _:
                            raise NotImplementedError(f"Unhandled state: {state}")

        return status_steps, final_agent_text, final_task_id


def send_streaming(user_text: str, container, data: dict | None, files: list[dict] | None) -> tuple[list[str], str, str | None]:
    return asyncio.run(_send_streaming(user_text, container, data, files))



# ── Artifact processing ────────────────────────────────────────────────────────

def _process_completed_task(task: Task) -> None:
    """Extract artifacts and finished status message from a completed Task."""
    if task.artifacts:
        for artifact in task.artifacts:
            entry = {
                "task_id": task.id,
                "artifact_id": artifact.artifact_id,
                "name": artifact.name or "Unnamed Artifact",
                "data": get_data_parts(artifact.parts) if artifact.parts else [],
            }
            st.session_state.artifacts.append(entry)


# ── UI rendering ───────────────────────────────────────────────────────────────

def render_sidebar() -> None:
    with st.sidebar:
        st.title("⚙️ Settings")
        st.session_state.streaming = st.toggle(
            "Streaming (SSE)", value=st.session_state.streaming
        )
        st.caption(
            "**Streaming**: Events arrive in real time via SSE.\n\n"
            "**Non-Streaming**: One blocking request, result on completion."
        )
        st.divider()
        st.markdown(f"**Session ID:** `{st.session_state.session_id}`")
        if st.session_state.task_id:
            st.markdown(f"**Task ID:** `{st.session_state.task_id}`")
        else:
            st.markdown("**Task ID:** *(new task)*")
        st.divider()
        if st.button("🔄 New Session", use_container_width=True):
            # Reset everything except streaming preference
            streaming = st.session_state.streaming
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            init_session_state()
            st.session_state.streaming = streaming
            st.rerun()


def render_chat_history() -> None:
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            # If this message was recorded with a trace, show it as a collapsed block
            if msg.get("status_steps"):
                task_id_info = f" (Task: {msg.get('task_id', 'unknown')})" if "task_id" in msg else ""
                with st.status(f"✅ Task trace{task_id_info}", expanded=False):
                    for step in msg["status_steps"]:
                        st.write(step)
            
            # Show the actual message text
            if msg.get("content"):
                st.markdown(msg["content"])


def render_artifacts() -> None:
    if st.session_state.artifacts:
        st.divider()
        st.subheader("📦 Artifacts")
        for a in st.session_state.artifacts:
            # Show both the Artifact Name and explicitly label the IDs
            label = f"**{a['name']}**"
            with st.expander(label):
                st.caption(f"**Task ID:** `{a.get('task_id', 'unknown')}`  |  **Artifact ID:** `{a['artifact_id']}`")
                if a["data"]:
                    for d in a["data"]:
                        st.json(d)
                else:
                    st.caption("No data parts in this artifact.")


def handle_user_input(user_text: str, data: dict | None, files: list[dict] | None) -> None:
    """Process a user message — decides streaming vs non-streaming."""
    # Append user message to history
    st.session_state.messages.append({"role": "user", "content": user_text})

    if st.session_state.streaming:
        # Streaming: st.status() expander inside the chat message shows all steps
        with st.chat_message("assistant"):
            container = st.container()

        status_steps, final_text, task_id_trace = send_streaming(user_text, container, data, files)

        # Persist to history! (Re-renders trace and final text correctly next time)
        st.session_state.messages.append({
            "role": "assistant",
            "content": final_text,
            "status_steps": status_steps,
            "task_id": task_id_trace
        })

    else:
        # Non-streaming: spinner while waiting for the result
        with st.chat_message("assistant"):
            with st.spinner("Waiting for agent response…"):
                task = send_non_streaming(user_text, data, files)
            
            agent_text = (
                get_message_text(task.status.message)
                if task.status.message else ""
            )
            
            if task.status.state == TaskState.input_required:
                st.session_state.task_id = task.id
                st.session_state.pending_prompt = agent_text
            elif task.status.state in (TaskState.completed, TaskState.rejected):
                st.session_state.task_id = None

            st.markdown(agent_text)
            st.session_state.messages.append({
                "role": "assistant", 
                "content": agent_text,
                "status_steps": None, # no trace in non-streaming mode
                "task_id": task.id
            })


# ── Main app ───────────────────────────────────────────────────────────────────

def main() -> None:
    st.set_page_config(
        page_title="A2A Agent Chat",
        page_icon="🤖",
        layout="wide",
    )
    init_session_state()
    render_sidebar()

    st.title("🤖 A2A Agent Chat")
    mode_badge = "🟢 Streaming" if st.session_state.streaming else "🔵 Non-Streaming"
    st.caption(f"Mode: **{mode_badge}** · Server: `{BASE_URL}`")
    st.divider()

    render_chat_history()
    render_artifacts()

    # ── Chat Input & Attachments ──────────────────────────────────────────
    with st.container():
        col1, col2 = st.columns([1, 1])
        with col1:
            uploaded_files = st.file_uploader(
                "Attach Files", accept_multiple_files=True, label_visibility="collapsed"
            )
        with col2:
            json_data_str = st.text_area(
                "Attach JSON Data", placeholder='{"key": "value"}', height=68, label_visibility="collapsed"
            )

        # Determine prompt based on whether we are continuing a task
        is_continuation = "pending_prompt" in st.session_state
        if is_continuation:
            prompt_text = f"Reply: {st.session_state.pending_prompt}"
        else:
            prompt_text = "Message the agent…"

        user_input = st.chat_input(prompt_text)
        
        if user_input:
            parsed_data = None
            if json_data_str.strip():
                try:
                    parsed_data = json.loads(json_data_str)
                except json.JSONDecodeError:
                    st.error("Invalid JSON provided in data attachment.")
                    st.stop()

            parsed_files = None
            if uploaded_files:
                parsed_files = []
                for uf in uploaded_files:
                    bytes_data = uf.getvalue()
                    b64_str = base64.b64encode(bytes_data).decode("utf-8")
                    parsed_files.append({
                        "name": uf.name,
                        "mime_type": uf.type,
                        "bytes": b64_str
                    })

            if is_continuation:
                del st.session_state.pending_prompt

            handle_user_input(user_input, data=parsed_data, files=parsed_files)
            st.rerun()


if __name__ == "__main__":
    main()
