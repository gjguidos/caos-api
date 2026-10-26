"""RG-02E trusted declarations, dependent catalogs, and review preconditions."""

import copy
import importlib
import json

import pytest

from src.base.entities.user import User
from src.domains.agentverse.registries.base import Registry
from src.domains.agentverse.security.ownership_review import record_hash, review_manifest
from src.domains.agentverse.services.registry_service import RegistryService

USER = User(id="owner", tenant_id="tenant")


@pytest.mark.parametrize(
    "body",
    [
        {"capabilities": {"required_tools": {"dep": "Display label"}}},
        {"capabilities": {"system_tools": {"analyze": "dep"}}},
        {"capabilities": {"optional_tools": {"dep": "Display label"}}},
        {"tools": [{"key": "dep", "name": "Different label"}]},
    ],
)
@pytest.mark.parametrize(
    "policy", [{}, {"ownership": "system"}, {"ownership": "tenant", "tenant_id": "foreign"}]
)
def test_role_maps_and_explicit_tool_keys_require_independent_ownership(body, policy):
    tool = Registry("tool")
    tool.register(name="dep", component=object, metadata=policy)
    catalog = RegistryService({"tool": tool})
    assert not catalog.client_metadata_allowed({"ownership": "global", **body}, USER)
    tool.register(name="dep", component=object, metadata={"ownership": "global"})
    assert catalog.client_metadata_allowed({"ownership": "global", **body}, USER)


@pytest.mark.parametrize(
    "body",
    [
        {"uses_workflows": ["child"]},
        {"capabilities": {"required_workflows": {"alias": "child"}}},
        {"runner": {"type": "workflow", "key": "child"}},
        {"command": "@workflow --workflow_key=child"},
        {"command": "@workflow --workflow_key child"},
    ],
)
def test_nested_workflows_cannot_hide_unknown_or_protected_dependencies(body):
    tools, flows = Registry("tool"), Registry("workflow")
    tools.register(name="workflow", component=object, metadata={"ownership": "global"})
    catalog = RegistryService({"tool": tools, "workflow": flows})
    parent = {"ownership": "global", **body}
    assert not catalog.client_metadata_allowed(parent, USER)
    flows.register(name="child", component=object, metadata={"ownership": "system"})
    assert not catalog.client_metadata_allowed(parent, USER)
    flows.register(name="child", component=object, metadata={"ownership": "global"})
    assert catalog.client_metadata_allowed(parent, USER)


@pytest.mark.parametrize(
    "command",
    [
        "@workflow",
        "@workflow --workflow_key=",
        "@workflow --workflow_key=${inputs.target}",
        "@workflow --workflow_key=a --workflow_key=b",
        "@workflow --workflow_key='bad",
    ],
)
def test_unresolved_workflow_commands_fail_closed(command):
    tools = Registry("tool")
    tools.register(name="workflow", component=object, metadata={"ownership": "global"})
    assert not RegistryService({"tool": tools}).client_metadata_allowed(
        {"ownership": "global", "command": command}, USER
    )


def test_review_manifest_preserves_absent_null_and_hashes_every_record_without_assignments():
    rows = [
        {
            "_id": "immutable-1",
            "id": "adam",
            "tenant_id": "tenant",
            "genesis_local_seed": True,
            "prompt": "private",
        },
        {"_id": "immutable-2", "id": "adam", "ownership": None},
        {},
    ]
    before = copy.deepcopy(rows)
    report = review_manifest(rows, source="dev.coll_agents")
    assert report[0]["expected_policy"]["ownership"] == {"present": False, "value": None}
    assert report[1]["expected_policy"]["ownership"] == {"present": True, "value": None}
    assert report[0]["blockers"] == ["duplicate_identifier"]
    assert "missing_identifier" in report[2]["blockers"]
    assert report[0]["record_sha256"] != record_hash({**rows[0], "prompt": "changed"})
    assert all(r["proposed_ownership"] is None and not r["apply_authorized"] for r in report)
    assert "private" not in json.dumps(report)
    assert rows == before


@pytest.mark.parametrize("module", ["chat_agent", "form_agent", "orchestration_agent"])
def test_only_reviewed_agent_specs_declare_global(module):
    from src.domains.agentverse.agents.specs.agent_spec import AgentSpec

    spec_module = importlib.import_module(f"src.domains.agentverse.agents.specs.{module}")
    specs = [value for value in vars(spec_module).values() if isinstance(value, AgentSpec)]
    assert len(specs) == 1 and specs[0].ownership == "global"
    assert all(ref.key for ref in specs[0].tools)
    payload = specs[0].model_dump(exclude={"ownership"})
    assert AgentSpec.model_validate(payload).ownership is None


@pytest.mark.parametrize("role", ["builtin", "crawler", "writer_assistant", "compliance_auditor"])
def test_reviewed_roles_keep_ownership_separate_from_internal_dependencies(role):
    module = importlib.import_module(f"src.domains.agentverse.agents.roles.{role}.spec")
    assert module.role_spec.to_metadata()["ownership"] == "global"
    # Classification never strips capability maps to manufacture catalog access.
    assert module.role_spec.to_metadata()["capabilities"] == module.role_spec.capabilities


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "policy",
    [
        {"ownership": "global", "tenant_scoped": True},
        {"ownership": "global", "tags": ["system"]},
        {"ownership": "tenant", "tenant_id": "tenant", "visibility": "private", "creator": "other"},
    ],
)
async def test_conversation_checks_complete_persisted_policy(policy):
    from src.base.events.v2.contracts import EventPermissionError
    from src.domains.agentverse.agents.lifecycle.tests.test_utu import setup
    from src.domains.agentverse.entities.conversation_v2 import ConversationV2

    env = setup()
    env.definitions.find_by_id.return_value = {"id": "agent", **policy}
    conversation = ConversationV2(
        tenant_id="tenant", member_user_ids=("owner",), agent_ids=("agent",)
    )
    with pytest.raises(EventPermissionError, match="target denied"):
        await env.utu.authorize_conversation(session_id="session", conversation=conversation)


def test_auditor_rulepack_hints_cannot_disclose_unclassified_rulepacks():
    packs = Registry("rulepack")
    packs.register(name="gdpr_compliance", component=object)
    catalog = RegistryService({"rulepack": packs})
    metadata = {"ownership": "global", "required_rulepacks_hint": ["gdpr_compliance"]}
    assert not catalog.client_metadata_allowed(metadata, USER)
    packs.register(name="gdpr_compliance", component=object, metadata={"ownership": "global"})
    assert catalog.client_metadata_allowed(metadata, USER)
