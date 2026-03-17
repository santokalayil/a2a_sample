# from a2a.client import (
#     ClientConfig, ClientEvent,
#     ClientCallContext, ClientCallInterceptor,
#     ClientFactory,
# )
# from a2a.types import (
#     Message,
#     MessageSendConfiguration,
#     MessageSendParams,
#     #
#     Task,
#     TaskArtifactUpdateEvent,
#     TaskQueryParams,
#     TaskState,
#     TaskStatus,
#     TaskPushNotificationConfig,
#     TaskStatusUpdateEvent,
#     TaskResubscriptionRequest,
#     TaskIdParams,
#     GetTaskRequest,
#     GetTaskResponse,
#     GetTaskSuccessResponse,
#     GetTaskPushNotificationConfigRequest,
#     GetTaskPushNotificationConfigResponse,
#     GetTaskPushNotificationConfigParams,
#     GetTaskPushNotificationConfigSuccessResponse
# )

import asyncio
from contextlib import aclosing
# from typing import Any, Awaitable

# from adapters.redis_taskstore import RedisTaskStoreAdapter


# async def main() -> None:
#     task_store = RedisTaskStoreAdapter()
#     key = "name"

#     await task_store.set(key, "Sanljljljlj")
#     value = await task_store.get(key)
#     print(f"{key=} | {value=}")
#     return

import httpx
from a2a.client import A2ACardResolver, Client, ClientFactory, ClientConfig, ClientEvent
from a2a.client.client import UpdateEvent
from a2a.types import AgentCard, Message, Part, TextPart, DataPart, Role, Task, TaskStatusUpdateEvent, TaskArtifactUpdateEvent
from a2a.types import TaskStatus, TaskState
from a2a.utils import get_data_parts, get_artifact_text
from a2a_server import models
from typing import AsyncIterator, AsyncGenerator
from dataclasses import dataclass
import uuid

HOST: str = "localhost"
PORT: int = 8001  # Servers' PORT and host
BASE_URL: str = f"http://{HOST}:{PORT}"

AUTH_TOKEN = "Bearer ABCD"



@dataclass
class InputRequiredResult:
    task_id: str
    agent_response: models.AgentResponse

@dataclass
class CompletedResult:
    agent_response: models.AgentResponse

@dataclass
class ErrorResult:
    state: str  # failed, rejected, etc.
    message: str

type Result = InputRequiredResult | CompletedResult | ErrorResult

def create_a2a_request_message(
        agent_request: models.AgentRequest,
        task_id: str | None = None,
    ) -> Message:
    parts = [Part(root=DataPart(data=agent_request.model_dump()))]
    msg = Message(
        message_id=uuid.uuid4().hex,  # NOTE: think of this later uuid.uuid4()
        context_id=agent_request.session_id,
        parts=parts,
        role=Role.user,
        # task_id=agent_request.task_id,
        task_id=task_id,  # NOTE: change it to add to task_id
    )
    return msg

def extract_a2a_response(message: Message) -> models.AgentResponse:
    data_parts = get_data_parts(message.parts)
    assert len(data_parts) == 1
    data = data_parts[0]
    return models.AgentResponse.model_validate(data)

from pathlib import Path

TEMP_DIR = Path(__file__).parent / ".temp"


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

        user_id = "ABC"
        session_id: str = "DEF"
        # client, agent_req, task_id
        agent_request = models.AgentRequest(
            user_id=user_id,
            session_id=session_id,
            request="Hi How you are you doing?",
        )
        msg = create_a2a_request_message(agent_request) # task_id also can be added
        # stream: AsyncGenerator[ClientEvent | Message, None] = client.send_message(msg)
        # gen: AsyncIterator[ClientEvent | Message] = aiter(stream)
        
        completed = False
        idx = 0
        while not completed:
            async with aclosing(client.send_message(msg)) as stream:
                async for item in stream:  # Client Event arikkum always
                    idx += 1
                    if isinstance(item, tuple):
                        task: Task = item[0]
                        (TEMP_DIR / f"{idx}_task.json").write_text(task.model_dump_json(indent=4), encoding="utf-8")

                        update_event: TaskStatusUpdateEvent | TaskArtifactUpdateEvent| None = item[1]  # UpdateEvent
                        assert update_event is not None, "Update Event is None, which is not so far seen"
                        if isinstance(update_event, TaskStatusUpdateEvent):
                            (TEMP_DIR / f"{idx}_task_update_event.json").write_text(update_event.model_dump_json(indent=4), encoding="utf-8")
                        else:  # isinstance(update_event, TaskArtifactUpdateEvent)
                            (TEMP_DIR / f"{idx}_t_artifact_update_event.json").write_text(update_event.model_dump_json(indent=4), encoding="utf-8")
                    
                        task_status: TaskStatus = task.status
                        match task_status.state:
                            case TaskState.submitted | TaskState.working:
                                print(f"[{session_id}] Task ({task.id}) is in {task_status.state} state")
                                if task_status.message:
                                    assert task_status.message.context_id, "Session Id not found!"
                                    session_id = task_status.message.context_id

                                    # NOTE: for now breaking. since we always wanted to create task
                                    assert task_status.message.task_id, "Task Id not found!"
                                    assert task_status.message.task_id == task.id, "Message TaskId and actual task id does not match!"
                                    assert task_status.message.parts
                                    for part in task_status.message.parts:
                                        if isinstance(part.root, DataPart):
                                            agent_response_data = part.root.data
                                            print("AGENT RESPONSE DATA PART >>>>>>", agent_response_data)
                                        elif isinstance(part.root, TextPart):
                                            agent_response_text = part.root.text
                                            print("AGENT RESPONSE TEXT PART >>>>>>", agent_response_text)
                                        else:
                                            raise NotImplementedError("Part is not a data part")
                                else:
                                    print("Task message not found")
                                    print("UPDATE EVENT =======>>", update_event.model_dump_json(indent=4))
                                    print("TASK_STATUS =======>>", task.status.model_dump_json(indent=4))
                                    # raise ValueError("Task Message is always expected")
                                    # completed = True

                            case TaskState.input_required:
                                print(f"[{session_id}] Task ({task.id}) is in {task_status.state} state")
                                if task_status.message:
                                    assert task_status.message.context_id, "Session Id not found!"
                                    session_id = task_status.message.context_id

                                    # NOTE: for now breaking. since we always wanted to create task
                                    assert task_status.message.task_id, "Task Id not found!"
                                    assert task_status.message.task_id == task.id, "Message TaskId and actual task id does not match!"
                                    agent_response: models.AgentResponse = extract_a2a_response(task_status.message)
                                    print(f"[{task.context_id}] Task [{task.id}] USER INPUT REQUIRED >>>>>>", agent_response)
                                    user_response = input(agent_response.response)
                                    # construct agent response with user_response
                                    agent_request = models.AgentRequest(
                                        user_id=user_id,
                                        session_id=session_id,
                                        request=user_response,
                                    )
                                    user_reply_msg = create_a2a_request_message(agent_request, task.id)
                                    msg = user_reply_msg  # for while loop
                                    # still task not completed, so break here and restart next iteration of while loop
                                    break
                                else:
                                    print("Error: task message is always expected")
                                    print(task.model_dump_json(indent=4))
                                    raise ValueError("Task Message is always expected")
                            case TaskState.completed:
                                print(f"[{session_id}] Task [{task.id}] completed >>>>>>>>")
                                completed = True  # to STOP while loop iteration
                                assert task_status.message, "task status must have message"
                                # print(task_status.message.model_dump_json(indent=4))
                                if task.artifacts:
                                    for artifact in task.artifacts:
                                        print(f"[{session_id}] Task [{task.id}] Artifact found with id {artifact.artifact_id}")
                                        if artifact.name:
                                            print(f"[{session_id}] Task [{task.id}] Artifact name {artifact.name}")
                                        if artifact.description:
                                            print(f"[{session_id}] Task [{task.id}] Artifact description {artifact.description}")
                                        if artifact.parts:
                                            print(f"[{session_id}] Task [{task.id}] >>> Artifact Parts")
                                            print(get_data_parts(artifact.parts))
                                            print(100 * "-")
                                else:
                                    print(f"[{session_id}] Task [{task.id}] Artifacts not found")
                                break
                            case TaskState.rejected:
                                print(f"[{session_id}] Task [{task.id}] is rejected due ....")
                                completed = True
                                break
                            case _:
                                print(f"[{session_id}] Task [{task.id}] has unexpected status: {task_status.state}")
                                raise NotImplementedError



                        # task_status.message
                    else:
                        print(type(item))
                        raise NotImplementedError
    return


def construct_status_message(message: Message) -> models.AgentResponse:
    ...


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

        user_id = "SANTO_USER"
        session_id: str = "SANTO_SESSION"
        # client, agent_req, task_id
        agent_request = models.AgentRequest(
            user_id=user_id,
            session_id=session_id,
            request="Hi How you are you doing?",
        )
        msg = create_a2a_request_message(agent_request)

        # send_message is ALWAYS an async generator (even in non-streaming mode).
        # Non-streaming: it makes one HTTP call, yields the final Task once, then stops.
        # Streaming: it yields (Task, UpdateEvent) tuples as SSE events arrive.
        # So you can NEVER await it — always use async for or anext().
        print(f"[{session_id}] Sending message...")
        reply = await anext(client.send_message(msg))   # grab the one-and-only item

        print(type(reply))

        # In non-streaming mode, reply is a (Task, None) tuple or just a Task
        assert isinstance(reply, tuple), "Reply is not a tuple"
        task, _ = reply
        print(f"[{task.context_id}] Task [{task.id}] state: {task.status.state}")
        if task.artifacts:
            for artifact in task.artifacts:
                print(f"[{task.context_id}] task_id [{task.id}] artifact_id [{artifact.artifact_id}]")
                print(artifact.model_dump_json(indent=4))
        # print(task.model_dump_json(indent=4))


if __name__ == "__main__":
    asyncio.run(streaming_main())
    

