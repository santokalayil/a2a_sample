
from .config import HOST, PORT

from a2a.types import AgentCard, AgentCapabilities, AgentSkill
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.apps import A2AFastAPIApplication
from a2a_redis import RedisTaskStore #, RedisStreamsEventQueue, RedisPushNotificationConfigStore
from a2a_redis.utils import create_redis_client

REDIS_HOST = "localhost"
REDIS_PORT = 6379
REDIS_PREFIX = "ca2as:tasks:"
REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/0"
REDIS_MAX_CONN = 50
CONN_ERR_MSG = "Redis is not connected. Call connect() first"

TASK_STORE = RedisTaskStore(
    redis_client=create_redis_client(url=REDIS_URL, max_connections=REDIS_MAX_CONN),
    prefix=REDIS_PREFIX,
)

agent_url = f"http://{HOST}:{PORT}"
agent_version = "0.1.0"
modes = ["text/plain"]
input_modes = modes.copy()
output_modes = modes.copy()

caps = AgentCapabilities(streaming=True, push_notifications=False, extensions=None)
skills = [
    AgentSkill(
        id="QnA_skill",
        name="QnA Skill",
        description="Answers questions from user",
        tags=[],
        input_modes=input_modes,
        output_modes=output_modes,
    )
]

CARD = AgentCard(
    name="QnA Agent",
    description="QnA Agent answers your questions",
    url=agent_url,
    version=agent_version,
    default_input_modes=input_modes,
    default_output_modes=output_modes,
    capabilities=caps,
    skills=skills,
)
from .executor import AgentExecutor
request_handler = DefaultRequestHandler(
    agent_executor=AgentExecutor(),
    task_store=TASK_STORE,
    # queue_manager=
    # push_config_store=
    # push_sender=
)

app = A2AFastAPIApplication(
    agent_card=CARD,
    http_handler=request_handler,
    max_content_length=10 * 1024 * 1024, # 10 MB
)

fastapi_app = app.build()
