import httpx
import json

from a2a.client import A2ACardResolver, Client, ClientFactory, ClientConfig
from a2a.types import PushNotificationConfig
from orchestrator_api.adapters import RedisAdapter

# Re-use the building blocks from the base client
from a2a_client import make_user_message, BASE_URL, AUTH_TOKEN


async def pump_stream_to_redis(session_id: str, stream, task_id: str, httpx_client: httpx.AsyncClient | None = None):
    """
    Background worker that fully drains the A2A Server SSE stream
    and publishes every event to Redis Pub/Sub.
    Publishes a terminal sentinel when done so Streamlit knows to close.
    """
    print(f"[{session_id}] >>> BackgroundTask pump_stream_to_redis STATED.", flush=True)
    redis_adapter = RedisAdapter()
    try:
        async for item in stream:
            print(f"[{session_id}] processing stream item...", flush=True)
            if not isinstance(item, tuple):
                continue

            task, update_event = item

            if update_event is None:
                state = task.status.state.value if task.status else "unknown"
            elif hasattr(update_event, "status"):
                state = update_event.status.state.value
            elif hasattr(update_event, "artifact"):
                state = "artifact"
            else:
                state = task.status.state.value if task.status else "unknown"

            payload = {
                "id": task_id,
                "task": task.model_dump(mode="json"),
                "state": state,
            }
            if state in ("input-required", "completed"):
                print(f"[{session_id}] DEBUG PAYLOAD: {json.dumps(payload, indent=2)}", flush=True)
            print(f"[{session_id}] Relay Event -> {state}", flush=True)
            await redis_adapter.publish(f"chat_stream:{session_id}", json.dumps(payload))
    except Exception as e:
        import traceback
        print(f"[{session_id}] Error pumping stream to Redis: {e}\n{traceback.format_exc()}", flush=True)
    finally:
        # Publish a sentinel so the Streamlit SSE loop knows the stream ended
        sentinel = json.dumps({"id": task_id, "state": "stream_complete", "task": {}})
        await redis_adapter.publish(f"chat_stream:{session_id}", sentinel)
        print(f"[{session_id}] <<< Stream complete. Sentinel published.", flush=True)
        
        # Keep connection alive until pump finishes, then close it safely
        if httpx_client:
            await httpx_client.aclose()


async def dispatch_task_to_agent(
    session_id: str,
    text: str,
    task_id: str | None = None,
    data: dict | None = None,
    files: list[dict] | None = None,
) -> tuple[str, object, httpx.AsyncClient]:
    """
    Opens a streaming A2A request, publishes the first event to Redis
    immediately, and returns (task_id, remaining_stream) for the caller
    to pass to FastAPI BackgroundTasks.
    """
    httpx_client = httpx.AsyncClient(timeout=120, headers={"token_key": AUTH_TOKEN})

    resolver = A2ACardResolver(base_url=BASE_URL, httpx_client=httpx_client)
    agent_card = await resolver.get_agent_card()
    if not agent_card:
        raise RuntimeError("Could not resolve agent card")

    client: Client = ClientFactory(
        ClientConfig(
            streaming=True, 
            httpx_client=httpx_client,
            push_notification_configs=[
                PushNotificationConfig(url=f"http://localhost:8002/webhook/{session_id}")
            ]
        )
    ).create(agent_card)

    msg = make_user_message(
        text=text,
        context_id=session_id,
        task_id=task_id,
        data=data,
        files=files,
    )

    stream = client.send_message(msg)

    # Pull the first event to learn the task_id, then publish it to Redis
    reply = await anext(stream)
    task = reply[0] if isinstance(reply, tuple) else reply
    true_task_id = task.id

    state = task.status.state.value if task.status else "submitted"
    first_payload = {
        "id": true_task_id,
        "task": task.model_dump(mode="json"),
        "state": state,
    }
    await RedisAdapter().publish(f"chat_stream:{session_id}", json.dumps(first_payload))
    print(f"[{session_id}] First event published: {state}", flush=True)

    # Hand off the remaining stream to the BackgroundTask, keeping httpx_client alive
    return true_task_id, stream, httpx_client
