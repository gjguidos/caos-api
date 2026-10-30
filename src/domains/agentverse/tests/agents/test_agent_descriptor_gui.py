"""Effective GUI composition on the existing Agent descriptor responses (read-only)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.domains.agentverse.entities.agent import Agent
from src.domains.agentverse.exceptions import AgentAccessDeniedError
from src.domains.agentverse.interfaces.api.v1.interface import (
    agent_descriptor,
    find_agent,
    find_all_agents,
)


def agent(**changes) -> Agent:
    base = {
        "id": "a1",
        "name": "Agent",
        "system_name": "agent",
        "type": "chat",
        "prompt": "p",
        "chat_url": "",
        "llm_type": "m",
        "audio_type": "",
        "db_type": None,
        "cache_type": None,
        "knowledge_db_type": None,
        "messaging_type": None,
        "dna_sequence": "d",
    }
    return Agent.model_validate({**base, **changes})


LEGACY_ENKI = agent(type="form", role_name="enki", runtime_version="v2")
V1_CHAT = agent(type="chat", runtime_version="v1")
THOTH = agent(
    type="chat",
    role_name="thoth",
    runtime_version="v2",
    gui_components=[{"type": "chat", "key": "conversation", "default": True}],
)


def summary(descriptor):
    return [(c["type"], c["key"], c["default"]) for c in descriptor["effective_gui_components"]]


def test_descriptor_only_adds_the_effective_gui() -> None:
    descriptor = agent_descriptor(LEGACY_ENKI)
    assert set(descriptor) - set(LEGACY_ENKI.model_dump(mode="json")) == {
        "effective_gui_components",
        "form_presentations",
    }
    # The persisted declaration is reported as stored; nothing is written back.
    assert descriptor["gui_components"] == []
    assert LEGACY_ENKI.gui_components == []


@pytest.mark.parametrize(
    "subject,expected",
    [
        (LEGACY_ENKI, [("form", "agent_configuration", True), ("chat", "conversation", False)]),
        (V1_CHAT, []),  # V1 keeps its existing type-based workspaces
        (THOTH, [("chat", "conversation", True)]),
    ],
)
def test_effective_gui_is_persisted_or_the_reviewed_default(subject, expected) -> None:
    assert summary(agent_descriptor(subject)) == expected


def test_effective_gui_carries_presentation_only() -> None:
    form = agent_descriptor(LEGACY_ENKI)["effective_gui_components"][0]
    assert set(form) == {"version", "key", "default", "type", "config"}
    assert form["config"] == {
        "source": {"kind": "contract", "contract": "caos.agent_creation_draft", "schema_version": 1}
    }


@pytest.mark.asyncio
async def test_existing_routes_return_descriptors_through_the_same_visibility_checks() -> None:
    user = SimpleNamespace(id="owner")
    service = SimpleNamespace(
        get_agent=AsyncMock(return_value=THOTH),
        list_agents=AsyncMock(return_value=[LEGACY_ENKI, V1_CHAT]),
    )
    one = await find_agent("a1", agent_service=service, user=user)
    many = await find_all_agents(agent_service=service, user=user)
    assert summary(one) == [("chat", "conversation", True)]
    assert [len(d["effective_gui_components"]) for d in many] == [2, 0]
    service.get_agent.assert_awaited_once_with("a1", user)
    service.list_agents.assert_awaited_once_with(user)
    # A visibility denial from AgentService propagates unchanged; no GUI is computed.
    service.get_agent.side_effect = AgentAccessDeniedError(
        user_id="x", agent_id="a1", action="read", reason="denied"
    )
    with pytest.raises(AgentAccessDeniedError):
        await find_agent("a1", agent_service=service, user=user)
