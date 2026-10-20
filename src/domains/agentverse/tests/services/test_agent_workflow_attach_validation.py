"""Attaching a workflow key with no catalog row fails the save.

Attach time and run time read the same ``(found, missing)`` fetch and apply
opposite policies to ``missing``: here it rejects the save, because a typo
belongs in an admin-visible 400 on create/PATCH rather than in a user-visible
failure the day someone clicks Run. ``required_tools_for`` skips it instead, so
a since-deleted workflow cannot brick an agent's other ones.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domains.agentverse.exceptions import AgentValidationError
from src.domains.agentverse.services.agent_service import AgentService


def _service(*, known_keys: set[str], required_tools: set[str] | None = None) -> AgentService:
    workflow_service = MagicMock()

    async def _get_workflows(keys):
        found = [MagicMock(key=k) for k in keys if k in known_keys]
        missing = [k for k in keys if k not in known_keys]
        return found, missing

    workflow_service.get_workflows = AsyncMock(side_effect=_get_workflows)
    workflow_service.required_tools_of = MagicMock(return_value=required_tools or set())

    return AgentService(
        agent_factory=None,
        db_service=None,
        audit_service=None,
        workflow_service=workflow_service,
    )


class TestUnknownKeysAreRejected:
    @pytest.mark.asyncio
    async def test_unknown_key_raises(self) -> None:
        service = _service(known_keys={"known_flow"})

        with pytest.raises(AgentValidationError) as excinfo:
            await service._validate_workflow_requirements(
                workflow_keys=["known_flow", "typo_flow"],
                rule_pack_ids=[],
            )

        assert "typo_flow" in str(excinfo.value)
        assert excinfo.value.data["missing_workflow_keys"] == ["typo_flow"]

    @pytest.mark.asyncio
    async def test_every_missing_key_is_named_at_once(self) -> None:
        service = _service(known_keys=set())

        with pytest.raises(AgentValidationError) as excinfo:
            await service._validate_workflow_requirements(
                workflow_keys=["gone_a", "gone_b"],
                rule_pack_ids=[],
            )

        assert excinfo.value.data["missing_workflow_keys"] == ["gone_a", "gone_b"]

    @pytest.mark.asyncio
    async def test_rejection_precedes_the_tool_requirement_lookup(self) -> None:
        """No point deriving requirements for a set that is already invalid."""
        service = _service(known_keys=set())

        with pytest.raises(AgentValidationError):
            await service._validate_workflow_requirements(
                workflow_keys=["gone"],
                rule_pack_ids=[],
            )

        service.workflow_service.required_tools_of.assert_not_called()

    @pytest.mark.asyncio
    async def test_existence_and_requirements_share_one_fetch(self) -> None:
        """Both questions are answered from a single catalog read - the keys are
        never fetched twice on the save path."""
        service = _service(known_keys={"a", "b"})

        await service._validate_workflow_requirements(
            workflow_keys=["a", "b"],
            rule_pack_ids=[],
        )

        service.workflow_service.get_workflows.assert_awaited_once_with(["a", "b"])


class TestKnownKeysPass:
    @pytest.mark.asyncio
    async def test_all_keys_present_proceeds(self) -> None:
        service = _service(known_keys={"a", "b"})

        assert (
            await service._validate_workflow_requirements(
                workflow_keys=["a", "b"],
                rule_pack_ids=[],
            )
            == set()
        )

    @pytest.mark.asyncio
    async def test_no_attached_workflows_needs_no_catalog_lookup(self) -> None:
        service = _service(known_keys=set())

        assert (
            await service._validate_workflow_requirements(workflow_keys=[], rule_pack_ids=[])
            == set()
        )
        service.workflow_service.get_workflows.assert_not_awaited()
