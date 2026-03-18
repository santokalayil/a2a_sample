from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import asyncio
import json
from a2a.types import Task

from .adapters import RedisAdapter
from .ports import StateStore, MessageBus
from .client import dispatch_task_to_agent, pump_stream_to_redis

app = FastAPI(title="Event-Driven A2A Orchestrator")

# Inject our Redis Adapter to fulfill both Ports
redis_adapter = RedisAdapter()
state_store: StateStore = redis_adapter
message_bus: MessageBus = redis_adapter

class ChatRequest(BaseModel):
    message: str
    task_id: str | None = None

# Keep strong references to background asyncio tasks so they don't get garbage collected
active_tasks = set()

@app.post("/chat/{session_id}")
async def start_chat(session_id: str, payload: ChatRequest):
    """
    1. Streamlit sends the user's message here.
    2. We open a streaming A2A connection to get the task_id.
    3. We register an asyncio task to relay remaining stream events to Redis.
    """
    await state_store.save_state(session_id, "latest_message", payload.message)

    task_id, remaining_stream, httpx_client = await dispatch_task_to_agent(
        session_id=session_id,
        text=payload.message,
        task_id=payload.task_id
    )

    # Create the background relay task and keep a strong reference
    task = asyncio.create_task(pump_stream_to_redis(session_id, remaining_stream, task_id, httpx_client))
    active_tasks.add(task)
    task.add_done_callback(active_tasks.discard)

    return {"status": "Task submitted", "session_id": session_id, "task_id": task_id}


@app.get("/stream/{session_id}")
async def stream_chat(session_id: str):
    """
    Streamlit connects here and holds the connection open (SSE).
    We yield events directly from the Redis Pub/Sub channel.
    """
    channel_name = f"chat_stream:{session_id}"

    async def event_generator():
        try:
            async for message_str in message_bus.subscribe(channel_name):
                yield f"data: {message_str}\n\n"
        except asyncio.CancelledError:
            pass

    return StreamingResponse(event_generator(), media_type="text/event-stream")
 
 
@app.post("/webhook/{session_id}")
async def handle_a2a_webhook(session_id: str, task: Task):
    """
    Called by the A2A Agent Server when a state transition occurs.
    We relay this directly to Redis Pub/Sub so any open UI streams see it.
    """
    state = task.status.state.value if task.status else "unknown"
    
    payload = {
        "id": task.id,
        "task": task.model_dump(mode="json"),
        "state": state,
    }
    
    print(f"[{session_id}] WEBHOOK RECEIVED -> {state}", flush=True)
    await redis_adapter.publish(f"chat_stream:{session_id}", json.dumps(payload))
    return {"status": "ok"}
