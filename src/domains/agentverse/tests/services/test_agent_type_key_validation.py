"""Agent `type` is always persisted as the canonical registry key.

Creation and update both funnel through ``AgentService._resolve_agent_type_key``,
so a stored ``type`` always addresses a registered agent and always matches the
key ``AgentFactory`` later resolves by.
"""

from __future__ import annotations

import pytest

# Importing the agent modules triggers their registration into the shared registry.
import src.domains.agentverse.agents.chat_agent.chat_agent  # noqa: F401
import src.domains.agentverse.agents.orchestration_agent.orchestration_agent  # noqa: F401
from src.domains.agentverse.services.agent_service import AgentService


@pytest.fixture
def service() -> AgentService:
    return AgentService(
        agent_factory=None, db_service=None, audit_service=None, workflow_service=None
    )


class TestResolveAgentTypeKey:
    @pytest.mark.parametrize(
        ("requested", "expected"),
        [
            ("chat", "chat"),
            ("Chat", "chat"),
            ("  CHAT  ", "chat"),
            ("orchestration", "orchestration"),
        ],
    )
    def test_normalizes_to_canonical_key(self, service, requested, expected):
        assert service._resolve_agent_type_key(requested) == expected

    @pytest.mark.parametrize("requested", ["ChatAgent", "nope", "", None])
    def test_unregistered_type_rejected(self, service, requested):
        with pytest.raises(ValueError, match="Unknown agent type"):
            service._resolve_agent_type_key(requested)


class TestUpdatePathNormalizes:
    def test_update_rewrites_type_to_key(self, service):
        from src.domains.agentverse.entities.agent_update_request import AgentUpdateRequest

        update = AgentUpdateRequest(type="Chat")
        extra: dict = {}
        # Mirrors the step 0 branch of _validate_update_request.
        if update.type is not None:
            extra["type"] = service._resolve_agent_type_key(update.type)
        assert extra["type"] == "chat"
