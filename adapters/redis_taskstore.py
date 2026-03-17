
from redis import Redis
import asyncio
from typing import Awaitable, Any


from a2a_orchestrator.ports import TaskStorePort


REDIS_HOST = "localhost"
REDIS_PORT = 6379


class RedisTaskStoreAdapter(TaskStorePort):
    _lock = asyncio.Lock()
    redis = Redis(REDIS_HOST, REDIS_PORT)

    @classmethod
    async def get(cls, key: str) -> Awaitable[bytes]:
        async with cls._lock:
            return cls.redis.get(key)
    
    @classmethod
    async def set(cls, key: str, value: Any) -> Awaitable[Any]:
        async with cls._lock:
            return cls.redis.set(key, value)


