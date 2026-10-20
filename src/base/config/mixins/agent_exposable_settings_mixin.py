from __future__ import annotations

from typing import Any, ClassVar


class AgentExposableSettingsMixin:
    """
    Mixin for settings sections that may expose a safe subset to agents.
    """

    expose_to_agents: ClassVar[bool] = False

    def public_dict(self) -> dict[str, Any]:
        return {}