import uuid
from a2a.server.agent_execution import AgentExecutor as BaseAE
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Message, TextPart, DataPart, FilePart, Part, TaskState, Role
from a2a.utils import get_data_parts, new_agent_text_message, new_agent_parts_message

from pydantic import ValidationError

from .models import AgentRequest, AgentResponse

class AgentExecutor(BaseAE):
    
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id: str | None = context.task_id
        context_id: str | None = context.context_id

        message: Message | None = context.message

        assert message
        assert task_id
        assert context_id
        
        data_parts = get_data_parts(message.parts)
        assert len(data_parts) == 1

        updater = TaskUpdater(
            event_queue=event_queue,
            task_id=task_id,
            context_id=context_id
        )


        user_id = data_parts[0].get("user_id")

        await updater.submit(
            create_agent_response_from_text(
                text="The Task is submitted. Validating the inputs...",
                session_id=context_id or "Unknown",
                task_id=task_id,
                user_id=user_id or "No User Id found!"
            )
        )
        print(f"[{context_id}] Task [{task_id}] Submitted the task")
        print(f"[{context_id}] Task [{task_id}] Validating the agent input")
        try:
            agent_request = AgentRequest.model_validate(data_parts[0])
        except ValidationError as e:
            error = f"AgentRequestValidation Error{str(e)}"
            print(f"[{context_id}] Task [{task_id}] {error}")
            await updater.reject(create_agent_response_from_text(error))
            return

        # check current status of task is in input requried etc. (not new to task store)
        task = context.current_task
        if task:
            if task.status.state == TaskState.input_required:
                print(f"[{context_id}] Task [{task_id}] is in input required state")
                print(task.model_dump_json(indent=4))
                assert task.history
                last_msg_in_history = task.history[-1]
                if last_msg_in_history.role == Role.user:
                    user_input_data = last_msg_in_history
                    if user_input_data.parts:
                        assert len(user_input_data.parts) == 1, "Multiple user inputs found. Not Implemented"
                        part = user_input_data.parts[0] 
                        assert isinstance(part.root, DataPart), "User input data is not a data part"
                        user_input = part.root.data["request"]
                        await updater.start_work(create_agent_response_from_text(f"Resuming Task from input required state, with the user input: {user_input}", task_id=task_id, session_id=context_id))
                    else:
                        raise NotImplementedError("User input data not found")
                elif last_msg_in_history.role == Role.agent:
                    # this means the last reply from user is not yet received.
                    # therefore we might need to resent input-required message
                    raise NotImplementedError("Resending input-required message is not yet implemented")
                else:
                    raise NotImplementedError(f"Unknown role: {last_msg_in_history.role}")
            else:
                raise NotImplementedError(f"Unknown task state: {task.status.state}")
        if not task:
            await updater.start_work(create_agent_response_from_text("Task Started", task_id=task_id, session_id=context_id))
            await updater.requires_input(
                message=create_agent_response_from_text(
                    text="User Input needed: select A, B, C",
                    task_id=task_id, 
                    session_id=context_id 
                ),
                final=True
            )
        # await updater.start_work()
        # await asyncio.sleep(10)
        print(f"[{context_id}] Task [{task_id}] Adding artifact")
        await updater.update_status(
            state=TaskState.working,
            message=create_agent_response_from_text(
                "Task is done. Adding to artifact....now",
                task_id=task_id,
                session_id=context_id
            )
        )
        artifact_id = uuid.uuid4().hex
        await updater.add_artifact(
            parts=[Part(root=DataPart(data={"MYRESULT": "MYRESULT", "INPUT": agent_request.model_dump()}))],
            name="PROCESSED RESULT",
            last_chunk=True,
            artifact_id=artifact_id,
        )
        message_id = uuid.uuid4().hex
        await updater.update_status(
            state=TaskState.working,
            message=Message(
                message_id=message_id,    
                context_id=context_id, 
                task_id=task_id,
                role=Role.agent,
                parts=[
                    Part(
                        root=TextPart(
                            text="Artificat added successfully"
                        )
                    ),
                    Part(
                        root=DataPart(
                            data={"message_id": message_id, "artifact_id": artifact_id, "INPUT": agent_request.model_dump()}
                        )
                    ),
                ]
            )
            # create_agent_response_from_text(
            #     f"Added artifact with id: {artifact_id}",
            #     task_id=task_id,
            #     session_id=context_id
            # )
        )
        await updater.complete(
            message=create_agent_response_from_text(
                "Task Completed",
                task_id=task_id,
                session_id=context_id
            )
        )
        print(f"[{context_id}] Task [{task_id}] completed >>>>>>>>")
        return 
    
    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        return await super().cancel(context, event_queue)



def create_agent_response_from_text(
        text: str, 
        user_id: str = "Unknown", 
        session_id: str = "Unknown",
        task_id: str | None = "Unknown",
    ) -> Message:
    agent_response = AgentResponse(
        user_id=user_id,
        session_id=session_id,
        response=text
    )
    a2a_response = new_agent_parts_message(
        parts=[Part(root=DataPart(data=agent_response.model_dump()))],
        context_id=session_id,
        task_id=task_id,
    )
    return a2a_response

