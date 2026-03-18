from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator

class StateStore(ABC):
    @abstractmethod
    async def save_state(self, session_id: str, key: str, value: str) -> None:
        """Saves a string value into the state store under a session and key."""
        pass

    @abstractmethod
    async def get_state(self, session_id: str, key: str) -> str | None:
        """Retrieves a string value from the state store."""
        pass

class MessageBus(ABC):
    @abstractmethod
    async def publish(self, channel: str, message: str) -> None:
        """Publishes a string message to a channel."""
        pass

    @abstractmethod
    async def subscribe(self, channel: str) -> AsyncGenerator[str, None]:
        """Subscribes to a channel and yields incoming messages."""
        pass
