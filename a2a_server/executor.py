import uuid
from typing import Any

from a2a.server.agent_execution import AgentExecutor as BaseAE
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import DataPart, FilePart, FileWithBytes, FileWithUri, Message, Part, TaskState, TextPart
from a2a.utils import new_agent_text_message


class AgentExecutor(BaseAE):

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id: str = context.task_id       # guaranteed non-None by SDK
        context_id: str = context.context_id # session id (managed by orchestrator)
        message: Message = context.message

        assert message
        assert task_id
        assert context_id

        updater = TaskUpdater(
            event_queue=event_queue,
            task_id=task_id,
            context_id=context_id,
        )

        # ── Signal: task received ──────────────────────────────────────
        await updater.submit(
            make_text_reply("Task submitted. Processing...", context_id, task_id)
        )
        print(f"[{context_id}] Task [{task_id}] submitted")

        # ── Extract content from all A2A Part types ────────────────────
        # Option 3: keep each part as an independent list item.
        # No merging of DataParts (avoids silent key overwrite),
        # no concatenation of TextParts (each is a distinct message unit).
        text_parts: list[str] = []
        data_parts: list[dict[str, Any]] = []
        file_parts: list[dict[str, Any]] = []  # holds parsed file metadata + content ref

        for part in message.parts:
            if isinstance(part.root, TextPart):
                text_parts.append(part.root.text)         # preserve order, no concat
            elif isinstance(part.root, DataPart):
                data_parts.append(part.root.data)         # preserve order, no merge
            elif isinstance(part.root, FilePart):
                f = part.root.file
                # filename is a first-class SDK field on the file object (NOT metadata)
                # metadata on FilePart is for arbitrary extras (e.g. tags, description)
                if isinstance(f, FileWithBytes):
                    file_parts.append({
                        "name": f.name,               # filename e.g. "document.pdf"
                        "mime_type": f.mime_type,
                        "bytes": f.bytes,             # base64-encoded content
                        "source": "bytes",
                        "extra": part.root.metadata,  # any caller-supplied extras
                    })
                elif isinstance(f, FileWithUri):
                    file_parts.append({
                        "name": f.name,               # filename e.g. "document.pdf"
                        "mime_type": f.mime_type,
                        "uri": f.uri,                 # URL to file content
                        "source": "uri",
                        "extra": part.root.metadata,
                    })
                else:
                    # SDK declares file as FileWithBytes | FileWithUri — closed union.
                    # If a future SDK version adds FileWithXxx, we want to know NOW,
                    # not silently drop the file and lose data.
                    raise NotImplementedError(
                        f"Unknown file variant: {type(f).__name__}. "
                        "SDK may have added a new FileWith* type — handle it explicitly."
                    )
                print(f"[{context_id}] Task [{task_id}] FilePart: name={f.name!r} mime={f.mime_type!r}")

        if not text_parts and not data_parts and not file_parts:
            print(f"[{context_id}] Task [{task_id}] No usable content in message parts")
            await updater.reject(
                make_text_reply("No text or data content found in message.", context_id, task_id)
            )
            return

        # NOTE: Use first TextPart as the primary user input (most common case)
        user_text: str = text_parts[0] if text_parts else ""
        print(f"[{context_id}] Task [{task_id}] text_parts={text_parts} data_parts={data_parts}")


        # ── Branch: new task vs continuation ──────────────────────────
        current_task = context.current_task  # None = new task, Task = continuation

        if current_task is None:
            # NEW TASK — ask user for input
            print(f"[{context_id}] Task [{task_id}] is a NEW task → asking for input")
            await updater.start_work(
                make_text_reply("Starting task...", context_id, task_id)
            )
            await updater.requires_input(
                message=make_text_reply(
                    "Please select an option: A, B, or C",
                    context_id,
                    task_id,
                )
            )
            return

        # EXISTING TASK — continuing from a previous state
        match current_task.status.state:
            case TaskState.input_required:
                print(f"[{context_id}] Task [{task_id}] continuing from input_required with: {user_text!r}")
                await updater.start_work(
                    make_text_reply(
                        f"Resuming task with your input: {user_text!r}",
                        context_id,
                        task_id,
                    )
                )

                # ── Add result as artifact ─────────────────────────────
                artifact_id = uuid.uuid4().hex
                print(f"[{context_id}] Task [{task_id}] adding artifact {artifact_id}")
                await updater.add_artifact(
                    parts=[
                        Part(root=DataPart(data={
                            "task_id": task_id,
                            "result": f"Processed user choice: {user_text}",
                            "text_parts": text_parts,   # all TextParts preserved as list
                            "data_parts": data_parts,   # all DataParts preserved as list
                            "file_parts": file_parts,   # all FileParts preserved as list
                        }))
                    ],
                    name="Task Result",
                    last_chunk=True,
                    artifact_id=artifact_id,
                )

                await updater.complete(
                    message=make_text_reply("Task completed successfully.", context_id, task_id)
                )
                print(f"[{context_id}] Task [{task_id}] completed >>>>>>>>")

            case _:
                print(f"[{context_id}] Task [{task_id}] unexpected state: {current_task.status.state}")
                raise NotImplementedError(
                    f"Cannot continue task in state: {current_task.status.state}"
                )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        return await super().cancel(context, event_queue)


# ── Helper ─────────────────────────────────────────────────────────────────────

def make_text_reply(text: str, context_id: str, task_id: str) -> Message:
    """Creates a native A2A agent Message with a single TextPart.

    Uses the SDK's new_agent_text_message() — no manual Message construction needed.
    context_id = session (managed by orchestrator, not user_id)
    """
    return new_agent_text_message(text=text, context_id=context_id, task_id=task_id)
