from typing import Protocol, Awaitable, Any

class TaskStorePort(Protocol):
    @classmethod
    async def get(cls, key: str) -> Awaitable[bytes]: ...

    @classmethod
    async def set(cls, key: str, value: Any) -> Awaitable[Any]: ...
