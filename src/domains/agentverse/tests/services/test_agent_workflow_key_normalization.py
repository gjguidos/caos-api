"""Attached workflow keys are always persisted as catalog slugs.

Creation and update both funnel through ``AgentService._normalize_workflow_keys``,
so ``Agent.workflows`` always holds the same spelling ``coll_workflows.key`` does.
This is what keeps ``WorkflowService._agents_pinned_to`` — an exact-match Mongo
query — able to see an attachment at all.
"""

from __future__ import annotations

import pytest

from src.domains.agentverse.entities.agent_update_request import AgentUpdateRequest
from src.domains.agentverse.entities.workflow import slugify_workflow_key
from src.domains.agentverse.services.agent_service import AgentService


@pytest.fixture
def service() -> AgentService:
    return AgentService(
        agent_factory=None, db_service=None, audit_service=None, workflow_service=None
    )


class TestNormalizeWorkflowKeys:
    @pytest.mark.parametrize(
        ("requested", "expected"),
        [
            (["my_workflow"], ["my_workflow"]),
            (["My Workflow"], ["my_workflow"]),
            (["  MY WORKFLOW  "], ["my_workflow"]),
            (
                ["Mini Workflow", "debug_two_mystic_images"],
                ["mini_workflow", "debug_two_mystic_images"],
            ),
        ],
    )
    def test_normalizes_to_catalog_slug(self, service, requested, expected):
        assert service._normalize_workflow_keys(requested) == expected

    def test_collapses_spellings_of_the_same_row(self, service):
        assert service._normalize_workflow_keys(["My Workflow", "my_workflow"]) == ["my_workflow"]

    def test_preserves_order_of_first_occurrence(self, service):
        keys = ["Beta Flow", "alpha_flow", "BETA FLOW"]
        assert service._normalize_workflow_keys(keys) == ["beta_flow", "alpha_flow"]

    @pytest.mark.parametrize("requested", [None, [], ["", "   "]])
    def test_empty_inputs_yield_no_keys(self, service, requested):
        assert service._normalize_workflow_keys(requested) == []


class TestUpdatePathNormalizes:
    def test_update_rewrites_workflows_to_slugs(self, service):
        update = AgentUpdateRequest(workflows=["My Workflow"])
        # Mirrors the branch in update_agent that runs before validation.
        if update.workflows is not None:
            update.workflows = service._normalize_workflow_keys(update.workflows)

        assert update.workflows == ["my_workflow"]
        assert update.model_dump_for_update()["workflows"] == ["my_workflow"]

    def test_unset_workflows_stay_out_of_the_update(self, service):
        update = AgentUpdateRequest(name="Rei")
        assert update.workflows is None
        assert "workflows" not in update.model_dump_for_update()


class TestPinnedQueryMatchesPersistedKeys:
    def test_delete_safety_query_sees_a_loosely_spelled_attachment(self, service):
        """``_agents_pinned_to`` matches on equality, so both sides must be slugs."""
        persisted = service._normalize_workflow_keys(["My Workflow"])
        # The key delete_workflow queries with, slugified the same way.
        queried = slugify_workflow_key("My Workflow")

        assert queried in persisted
