from __future__ import annotations

from typing import Any

from src.base.config.mixins.agent_exposable_settings_mixin import (
    AgentExposableSettingsMixin,
)


def build_public_agent_settings(settings: Any) -> dict[str, Any]:
    """
    Build public settings for agents.

    Platform rule:
    - Root Settings is a plain composition object.
    - Settings sections are discovered from instance attributes.
    - A settings section is private by default.
    - Only sections inheriting AgentExposableSettingsMixin may be exposed.
    - A section is exposed only when expose_to_agents=True.
    - Only public_dict() output is exposed.
    """

    public_settings: dict[str, Any] = {}

    for name, section in vars(settings).items():
        if name.startswith("_") or section is None:
            continue

        if not isinstance(section, AgentExposableSettingsMixin):
            continue

        if not section.expose_to_agents:
            continue

        public_settings[name] = section.public_dict()

    return public_settings