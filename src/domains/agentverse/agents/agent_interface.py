from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class AgentInterface(ABC):
    """Minimal, cycle-safe contract for agents."""

    @property
    @abstractmethod
    def id(self) -> str: ...

    @property
    @abstractmethod
    def user_id(self) -> str: ...

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def type(self) -> str: ...

    @abstractmethod
    async def respond(self, user_input: str, file: Any | None = None) -> str: ...
