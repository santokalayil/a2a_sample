from typing import AsyncGenerator
from .ports import StateStore, MessageBus

class RedisAdapter(StateStore, MessageBus):
    """
    Implements both the StateStore and MessageBus interfaces using Redis.
    Uses aioredis/redis-py asyncio interface.
    """
    def __init__(self, redis_url: str = "redis://localhost:6379"):
        # aioredis is now integrated into standard redis.asyncio
        from redis import asyncio as redis
        self.client = redis.from_url(redis_url, decode_responses=True)

    async def save_state(self, session_id: str, key: str, value: str) -> None:
        redis_key = f"session:{session_id}:{key}"
        await self.client.set(redis_key, value)

    async def get_state(self, session_id: str, key: str) -> str | None:
        redis_key = f"session:{session_id}:{key}"
        return await self.client.get(redis_key)

    async def publish(self, channel: str, message: str) -> None:
        await self.client.publish(channel, message)

    async def subscribe(self, channel: str) -> AsyncGenerator[str, None]:
        pubsub = self.client.pubsub()
        await pubsub.subscribe(channel)
        
        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    yield str(message["data"])
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
