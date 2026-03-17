import asyncio
import uuid
from contextlib import aclosing
from pathlib import Path

import httpx
from a2a.client import A2ACardResolver, Client, ClientFactory, ClientConfig
from a2a.client.client import UpdateEvent
from a2a.types import (
    AgentCard, Message, Part, TextPart, DataPart, Role,
    Task, TaskStatusUpdateEvent, TaskArtifactUpdateEvent,
    TaskStatus, TaskState, FilePart, FileWithBytes
)
from a2a.utils import get_data_parts, get_message_text, get_text_parts

HOST: str = "localhost"
PORT: int = 8001
BASE_URL: str = f"http://{HOST}:{PORT}"
AUTH_TOKEN = "Bearer ABCD"

TEMP_DIR = Path(__file__).parent / ".temp"


# ── Message builders ───────────────────────────────────────────────────────────

def make_user_message(
    text: str,
    context_id: str,
    task_id: str | None = None,
    data: dict | None = None,
    files: list[dict[str, str | bytes]] | None = None,
) -> Message:
    """Creates a native A2A user Message handling Text, Data, and Files.

    - text  → always sent as TextPart (the user's readable message)
    - data  → optional dict, sent as DataPart
    - files → optional list of dicts: {"name": "foo.pdf", "bytes": base64_str, "mime_type": "..."}
              sent as FilePart with FileWithBytes.
    """
    parts: list[Part] = [Part(root=TextPart(text=text))]
    
    if data:
        parts.append(Part(root=DataPart(data=data)))
        
    if files:
        for f in files:
            parts.append(Part(root=FilePart(
                file=FileWithBytes(
                    bytes=f["bytes"], 
                    name=f["name"], 
                    mime_type=f.get("mime_type")
                )
            )))

    return Message(
        message_id=uuid.uuid4().hex,
        role=Role.user,
        context_id=context_id,
        task_id=task_id,
        parts=parts,
    )


def extract_text_from_message(message: Message) -> str:
    """Reads all TextParts from an A2A Message joined as one string.
    This is a thin wrapper over the SDK's get_message_text().
    """
    return get_message_text(message)


# ── Streaming client ───────────────────────────────────────────────────────────

async def streaming_main() -> None:
    async with httpx.AsyncClient(
        timeout=600, headers={"token_key": AUTH_TOKEN},
    ) as httpx_client:
        resolver = A2ACardResolver(base_url=BASE_URL, httpx_client=httpx_client)
        agent_card: AgentCard | None = await resolver.get_agent_card()
        assert agent_card is not None, "Could not resolve agent card"

        client: Client = ClientFactory(
            ClientConfig(streaming=True, httpx_client=httpx_client)
        ).create(agent_card)

        # session_id = orchestrator-managed session (replaces user_id)
        session_id: str = "DEF"
        msg = make_user_message("Hi! How are you doing?", context_id=session_id)

        completed = False
        idx = 0
        while not completed:
            async with aclosing(client.send_message(msg)) as stream:
                async for item in stream:
                    idx += 1
                    if not isinstance(item, tuple):
                        print(f"Unexpected item type: {type(item)}")
                        raise NotImplementedError

                    task: Task = item[0]
                    update_event: TaskStatusUpdateEvent | TaskArtifactUpdateEvent | None = item[1]
                    assert update_event is not None

                    # Save debug snapshots
                    (TEMP_DIR / f"{idx}_task.json").write_text(
                        task.model_dump_json(indent=4), encoding="utf-8"
                    )
                    if isinstance(update_event, TaskStatusUpdateEvent):
                        (TEMP_DIR / f"{idx}_task_update_event.json").write_text(
                            update_event.model_dump_json(indent=4), encoding="utf-8"
                        )
                    else:
                        (TEMP_DIR / f"{idx}_t_artifact_update_event.json").write_text(
                            update_event.model_dump_json(indent=4), encoding="utf-8"
                        )

                    task_status: TaskStatus = task.status

                    match task_status.state:

                        case TaskState.submitted | TaskState.working:
                            print(f"[{session_id}] Task ({task.id}) → {task_status.state}")
                            if task_status.message:
                                session_id = task_status.message.context_id or session_id
                                # Read text directly — no custom model unwrapping
                                agent_text = extract_text_from_message(task_status.message)
                                if agent_text:
                                    print(f"[{session_id}]   Agent says: {agent_text!r}")
                            else:
                                # working with no message = just a state heartbeat, keep waiting
                                print(f"[{session_id}] Task ({task.id}) working (no message)")

                        case TaskState.input_required:
                            print(f"[{session_id}] Task ({task.id}) → INPUT REQUIRED")
                            if not task_status.message:
                                print(task.model_dump_json(indent=4))
                                raise ValueError("input_required state must carry a message to show the user")

                            session_id = task_status.message.context_id or session_id

                            # Read the agent's prompt directly from TextPart
                            prompt = extract_text_from_message(task_status.message)
                            if not prompt:
                                raise ValueError("input_required message has no text content")

                            print(f"[{session_id}] Task [{task.id}] Agent asks: {prompt!r}")
                            user_reply = input(f"  → {prompt} ")

                            # Send reply: same context_id + same task_id (continuation)
                            msg = make_user_message(user_reply, context_id=session_id, task_id=task.id)
                            break  # exit async for → while loop sends new msg

                        case TaskState.completed:
                            print(f"[{session_id}] Task [{task.id}] COMPLETED >>>>>>>>")
                            completed = True  # stop while loop

                            if task_status.message:
                                text = extract_text_from_message(task_status.message)
                                if text:
                                    print(f"[{session_id}]   Agent: {text!r}")

                            if task.artifacts:
                                for artifact in task.artifacts:
                                    print(f"[{session_id}] Artifact [{artifact.artifact_id}] name={artifact.name!r}")
                                    if artifact.parts:
                                        data_parts = get_data_parts(artifact.parts)
                                        for d in data_parts:
                                            print(f"   data: {d}")
                                        text_parts = get_text_parts(artifact.parts)
                                        for t in text_parts:
                                            print(f"   text: {t!r}")
                                    print("-" * 80)
                            else:
                                print(f"[{session_id}] No artifacts in completed task")
                            break

                        case TaskState.rejected:
                            print(f"[{session_id}] Task [{task.id}] REJECTED")
                            if task_status.message:
                                text = extract_text_from_message(task_status.message)
                                print(f"   Reason: {text!r}")
                            completed = True
                            break

                        case _:
                            print(f"[{session_id}] Task [{task.id}] unexpected state: {task_status.state}")
                            raise NotImplementedError(f"Unhandled state: {task_status.state}")


# ── Non-streaming client (for reference) ──────────────────────────────────────

async def non_streaming_main() -> None:
    async with httpx.AsyncClient(
        timeout=600, headers={"token_key": AUTH_TOKEN},
    ) as httpx_client:
        resolver = A2ACardResolver(base_url=BASE_URL, httpx_client=httpx_client)
        agent_card: AgentCard | None = await resolver.get_agent_card()
        assert agent_card is not None, "Could not resolve agent card"

        client: Client = ClientFactory(
            ClientConfig(streaming=False, httpx_client=httpx_client)
        ).create(agent_card)

        session_id: str = "SANTO_SESSION"
        msg = make_user_message("Hi! How are you doing?", context_id=session_id)

        # send_message is ALWAYS an async generator, even in non-streaming mode.
        # Non-streaming: one HTTP call → yields one (Task, None) tuple → stops.
        reply = await anext(client.send_message(msg))

        assert isinstance(reply, tuple), "Expected (Task, None) tuple in non-streaming mode"
        task, _ = reply
        print(f"[{task.context_id}] Task [{task.id}] state: {task.status.state}")

        if task.artifacts:
            for artifact in task.artifacts:
                print(f"  Artifact [{artifact.artifact_id}] name={artifact.name!r}")
                print(artifact.model_dump_json(indent=4))


if __name__ == "__main__":
    asyncio.run(streaming_main())
