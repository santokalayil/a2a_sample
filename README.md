# Event-Driven A2A Orchestrator

A robust, 3-tier integration demonstrating an asynchronous, event-driven orchestration pattern for A2A Agents using **Redis Pub/Sub** and **FastAPI**.

This project decouples the frontend UI from the backend agent execution by introducing an Orchestrator API that handles state management and real-time event relay.

## Architecture: Hybrid Event-Driven

The system uses a **redundant hybrid pattern** for maximum reliability:
1.  **SSE Relay (Live Traces)**: Sub-millisecond updates during active sessions.
2.  **Push Notifications (Webhooks)**: Persistent, stateless callbacks from the Agent for long-running background tasks.

```mermaid
graph TB
    subgraph Client ["Frontend (Streamlit)"]
        UI["Web UI :8501"]
    end

    subgraph Orchestrator ["Orchestrator Layer (FastAPI) :8002"]
        API["REST API<br/>/chat, /stream, /webhook"]
        Pump["Stream Pump<br/>(Background asyncio)"]
        Adapters["Adapters<br/>(Ports & Adapters)"]
    end

    subgraph Infra ["Infrastructure"]
        RedisPubSub["Redis Pub/Sub<br/>(Event Bus)"]
        RedisState["Redis KV<br/>(Session State)"]
    end

    subgraph AgentLayer ["Agent Layer :8001"]
        Agent["A2A Agent Server"]
        TaskStore["Redis Task Store"]
    end

    %% Flow: Initial Request
    UI -- "1. POST /chat" --> API
    API -- "2. Check State" --> RedisState
    API -- "3. Dispatch Task" --> Agent

    %% Flow: Path A (Real-time SSE Relay)
    Agent -. "4a. SSE Stream (Traces)" .-> Pump
    Pump -- "5a. Publish Event" --> RedisPubSub

    %% Flow: Path B (Background Webhook Callback)
    Agent -- "4b. POST /webhook (State Change)" --> API
    API -- "5b. Publish Event" --> RedisPubSub

    %% Flow: UI Update
    UI -- "6. GET /stream (SSE)" --> API
    API -- "7. Subscribe" --> RedisPubSub
    RedisPubSub -- "8. Relay Events" --> API
    API -- "9. Update UI" --> UI

    %% Styling
    style RedisPubSub fill:#f96,stroke:#333,stroke-width:2px
    style RedisState fill:#f96,stroke:#333,stroke-width:2px
    style Agent fill:#6b4,stroke:#333,stroke-width:2px
    style UI fill:#4af,stroke:#333,stroke-width:2px
```

1.  **A2A Agent Server (Port 8001)**: Native agent execution. Now configured with `HttpPushNotificationSender` to ping the Orchestrator on every state change.
2.  **Orchestrator API (Port 8002)**: The stateless hub. Receives both live streams and background webhooks, unifying them in Redis.
3.  **Streamlit UI (Port 8501)**: Consumes the unified event stream from Redis.

## Key Features

- **Decoupled Orchestration**: The UI never talks directly to the Agent Server.
- **Event Relay Pattern**: Orchestrator "pumps" SDK events into Redis Pub/Sub, allowing multiple subscribers and resilient stream handling.
- **Task Persistence**: Supports `input-required` multi-turn prompts by persisting `task_id` and routing follow-up replies to existing background tasks.
- **Pluggable Adapters**: Built with `StateStore` and `MessageBus` ports, currently using `RedisAdapter`.

## Infrastructure

This project requires **Redis** for state and messaging. Run it using Docker:

```bash
docker run -d --name a2a-redis -p 6379:6379 redis:alpine
```

## Setup & Running

This project uses `uv` for dependency management.

### 1. Start the Agent Server
```bash
uv run python -m a2a_server
```

### 2. Start the Orchestrator API
```bash
uv run uvicorn orchestrator_api.main:app --port 8002
```

### 3. Start the Streamlit UI
```bash
uv run streamlit run streamlit_client.py
```

## Using the UI

1. **New Session**: Generates a fresh UUID in the sidebar.
2. **Streaming Mode**: Real-time `st.status` traces powered by Redis SSE.
3. **Artifacts**: Automatic extraction and rendering of structured data produced by the agent.
4. **Context Recovery**: Responding to an "Input Required" prompt preserves the background task context.
