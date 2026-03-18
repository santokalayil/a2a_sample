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

import asyncio
import base64
import json
import uuid

import httpx
import requests
import streamlit as st

# We no longer need the a2a SDK directly in the UI!
# The UI is now a pure frontend talking to the Orchestrator API.

# ── Config ────────────────────────────────────────────────────────────────────
ORCHESTRATOR_URL = "http://localhost:8002"


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


# ── Non-streaming call ─────────────────────────────────────────────────────────

def send_non_streaming(user_text: str, data: dict | None, files: list[dict] | None) -> dict:
    """Send a message to the Orchestrator and wait for completion (Polling)."""
    # 1. Start the task via HTTP POST
    payload = {"message": user_text}
    # (In a full implementation, you'd pass data/files in the payload here and have the 
    # orchestrator API map them to make_user_message. For brevity, just passing text.)
    response = requests.post(
        f"{ORCHESTRATOR_URL}/chat/{st.session_state.session_id}", 
        json=payload
    )
    response.raise_for_status()
    
    # 2. Poll the state store via orchestrator API (Need to implement GET /status in API later)
    # For now, we will raise NotImplementedError as Streaming is the focus of this PR
    raise NotImplementedError("Non-streaming polling mode against the Orchestrator is pending implementation.")


# ── Streaming call ─────────────────────────────────────────────────────────────

async def _listen_to_sse(container, session_id: str) -> tuple[list[str], str, str | None]:
    """Connect to the Orchestrator's SSE endpoint and yield realtime Redis events."""
    
    status_steps: list[str] = []
    final_agent_text: str = ""
    final_task_id: str | None = None
    
    # State → (icon, label, expander_state)
    STATE_META = {
        "submitted":      ("📨", "Submitted",       "running"),
        "working":        ("⚙️", "Working",         "running"),
        "input-required": ("⏸️", "Input Required",  "running"),
        "completed":      ("✅", "Completed",       "complete"),
        "rejected":       ("❌", "Rejected",        "error"),
    }

    with container.status("🔄 Connecting to Orchestrator SSE Stream…", expanded=True) as status_box:
        
        url = f"{ORCHESTRATOR_URL}/stream/{session_id}"
        
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("GET", url) as stream:
                    async for chunk in stream.aiter_lines():
                        if not chunk.startswith("data:"):
                            continue
                            
                        # Parse the JSON payload natively from the Orchestrator's Redis Pub/Sub
                        data_str = chunk.replace("data: ", "").strip()
                        if not data_str:
                            continue
                            
                        payload = json.loads(data_str)
                        state_str = payload.get("state", "working")
                        final_task_id = payload.get("id")
                        st.session_state.task_id = final_task_id
                        
                        icon, label, _ = STATE_META.get(state_str, ("❓", state_str, "running"))
                        
                        # Just grab text directly out of the raw payload
                        agent_text = ""
                        task_obj = payload.get("task", {})
                        if "status" in task_obj and task_obj["status"] and "message" in task_obj["status"] and task_obj["status"]["message"]:
                            # Crude extraction purely for the UI mockup
                            parts = task_obj["status"]["message"].get("parts", [])
                            agent_text = " ".join([p.get("text", "") for p in parts if "text" in p])
                        
                        step_str = f"{icon} **{label}** — {agent_text}" if agent_text else f"{icon} **{label}**"
                        if not status_steps or status_steps[-1] != step_str:
                            status_box.write(step_str)
                            status_steps.append(step_str)
                            
                        if state_str == "input-required":
                            st.session_state.pending_prompt = agent_text
                            status_box.update(label="⏸️ Waiting for your input…", state="running", expanded=True)
                            return status_steps, agent_text, final_task_id
                            
                        elif state_str == "completed":
                            _process_completed_task_payload(payload)
                            st.session_state.task_id = None
                            status_box.update(label="✅ Task completed", state="complete", expanded=False)
                            # If no specific message was found in the final status, use a fallback
                            if not agent_text:
                                agent_text = "Task completed successfully!"
                            return status_steps, agent_text, final_task_id
                            
                        elif state_str == "rejected":
                            st.session_state.task_id = None
                            status_box.update(label="❌ Task rejected", state="error", expanded=True)
                            return status_steps, agent_text, final_task_id
                        
                        elif state_str == "stream_complete":
                            # Sentinel published when background SSE relay from A2A server finishes
                            st.session_state.task_id = None
                            status_box.update(label="✅ Stream complete", state="complete", expanded=False)
                            return status_steps, agent_text, final_task_id
                            
                        else:
                            status_box.update(label=f"{icon} {label}…", state="running")
        except Exception as e:
            status_box.update(label=f"❌ Connection Error: {e}", state="error")
            return status_steps, str(e), final_task_id

    return status_steps, final_agent_text, final_task_id




async def _do_stream(container, session_id, user_text, task_id):
    async def fire_post():
        await asyncio.sleep(0.3)  # Ensure the SSE listener is fully connected first
        async with httpx.AsyncClient() as client:
            payload = {"message": user_text}
            if task_id:
                payload["task_id"] = task_id
            await client.post(f"{ORCHESTRATOR_URL}/chat/{session_id}", json=payload)
            
    # Start the POST request concurrently in the background
    asyncio.create_task(fire_post())
    
    # While it's sleeping, instantly lock into listening onto the SSE stream!
    return await _listen_to_sse(container, session_id)

def send_streaming(user_text: str, container, data: dict | None, files: list[dict] | None) -> tuple[list[str], str, str | None]:
    """1. Listen to SSE Stream. 2. POST to orchestrator concurrently."""
    return asyncio.run(_do_stream(container, st.session_state.session_id, user_text, st.session_state.get("task_id")))

# ── Artifact processing ────────────────────────────────────────────────────────

def _process_completed_task_payload(payload: dict) -> None:
    """Extract artifacts from the raw JSON payload the Orchestrator delivered."""
    task_obj = payload.get("task", {})
    # Pydantic may serialize empty artifacts as null, so safely fall back to []
    artifacts = task_obj.get("artifacts") or []
    task_id = payload.get("id")
    
    for artifact in artifacts:
        raw_parts = artifact.get("parts") or []
        data_parts = []
        for p in raw_parts:
            # The part data is flattened by Pydantic's mode="json" dumping
            if "data" in p and p["data"]:
                data_parts.append(p["data"])
                
        entry = {
            "task_id": task_id,
            # SDK serializes UUID as camelCase "artifactId"
            "artifact_id": artifact.get("artifactId", "Unknown"),
            "name": artifact.get("name") or "Unnamed Artifact",
            "data": data_parts,
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

    st.title("🤖 Orchestrator Chat")
    mode_badge = "🟢 Streaming (Redis SSE)" if st.session_state.streaming else "🔵 Non-Streaming (Polling)"
    st.caption(f"Mode: **{mode_badge}** · Orchestrator Server: `{ORCHESTRATOR_URL}`")
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
