"""CONVERSATION-0.1A: the Conversation Tool and registry-driven conversation composition.

Registration, attachment and compatibility describe availability, never authority.
No database: integration with real Redis/UTU is in ``test_conversation_tool_integration``.
"""

import copy
import dataclasses
import json
import pathlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from src.base.entities.user import User
from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.activities.registry import (
    ActivityContractRegistry,
    ResourceResolverRegistry,
)
from src.domains.agentverse.activities.tests.test_activity_contracts import Resolver
from src.domains.agentverse.agents.conversational_composition_v2 import (
    LEGACY_CONVERSATION_COMPATIBILITY,
    ConversationGate,
    ConversationUnavailable,
    resolve_conversation,
)
from src.domains.agentverse.agents.creation_drafts.activity import AGENT_CREATION_INTERVIEW
from src.domains.agentverse.agents.factory import CORE_TOOL_KEYS, AgentFactory
from src.domains.agentverse.catalog import ResourceCatalog
from src.domains.agentverse.catalog.tests.test_resource_catalog import resolve
from src.domains.agentverse.dependencies.genesis_local import ENKI_PROVISION, THOTH_PROVISION
from src.domains.agentverse.entities.gui_components import effective_gui_components
from src.domains.agentverse.nataraja.plan import evaluate
from src.domains.agentverse.nataraja.tests.test_nataraja import (
    ENKI,
    baseline,
    enki_document,
    local_env,  # noqa: F401 - fixture re-export
    manifest,  # noqa: F401 - fixture re-export
)
from src.domains.agentverse.registries import tool_registry_instance
from src.domains.agentverse.registries.base import Registry
from src.domains.agentverse.registries.tool_registry import ToolRegistry
from src.domains.agentverse.services.registry_service import RegistryService
from src.domains.agentverse.services.workflow_service import WorkflowService
from src.domains.agentverse.tests.tools.test_data_validation_tool import FakeWorkflowDB
from src.domains.agentverse.tools.base import BaseTool
from src.domains.agentverse.tools.chat_tool_v2 import ChatToolV2
from src.domains.agentverse.tools.conversation_tool import (
    ConversationTool,
    validate_conversation_tool,
)
from src.domains.agentverse.tools.conversation_tool.spec import (
    ConversationToolConfig,
    conversation_tool,
)
from src.domains.agentverse.tools.spec import AgentToolRef
from src.domains.agentverse.tools.tool_manager_v1.registry_adapter import prepare_factories
from src.domains.agentverse.tools.tool_manager_v1.routing import route_for_v1
from src.domains.agentverse.tools.validation_tool.agent_validator import AgentDefinitionValidator
from src.domains.agentverse.tools.validation_tool.models import AgentCreationInput

INTERVIEW = "agent.creation_interview@1.0.0"
SRC = pathlib.Path(__file__).resolve().parents[5]  # .../src


@pytest.fixture
def tools(monkeypatch):
    monkeypatch.setattr(Registry, "_instances", dict(Registry._instances))
    registry = ToolRegistry()
    registry.bind_spec(conversation_tool)(ConversationTool)
    return registry


def definition(**changes):
    return {"runtime_version": "v2", "type": "chat", "role_name": "writer", "tools": [], **changes}


def attached(config=None, **changes):
    return definition(tools=[{"key": "conversation", "config": config or {}}], **changes)


# --- Registration and exact version ---------------------------------------------------------


def test_registered_optional_tool_declaring_the_existing_conversation_actions(tools):
    registered = tool_registry_instance.get("conversation", "1.0.0")
    assert issubclass(registered, BaseTool) and registered.key == "conversation"
    assert "conversation" not in CORE_TOOL_KEYS  # Optional: never auto-attached.
    info = tools.get_info("conversation")
    assert (info.version, info.metadata["scope"], info.metadata["exposure"]) == (
        "1.0.0",
        "agent",
        "public",
    )
    assert info.metadata["tool_class"] == "conversational"
    assert info.metadata["execution_mode"] == "synaptic_only"
    assert info.metadata["synaptic"]["capabilities"] == [
        {
            "signal": "chat.context",
            "access": "granted",
            "trusted_lines": [],
            "version": "1.0.0",
            "provider": "tool.chat",
        },
        {
            "signal": "chat.response",
            "access": "granted",
            "trusted_lines": [],
            "version": "1.0.0",
            "provider": "cognitive.chat",
        },
    ]
    validate_conversation_tool(tools)
    # Legacy Tools are untouched in this milestone.
    assert tool_registry_instance.get("chat_v2", "2.0.0") is ChatToolV2


@pytest.mark.parametrize(
    "failure",
    ["missing", "version", "extra", "missing_action", "provider", "access", "trusted_line"],
)
def test_startup_rejects_a_drifted_declaration(tools, failure):
    info = tools.get_info("conversation")
    actions = info.metadata["synaptic"]["capabilities"]
    if failure == "missing":
        tools.unregister("conversation")
    elif failure == "version":
        info.version = "1.0.1"
    elif failure == "extra":
        actions.append({**actions[0], "signal": "agent.draft.propose"})
    elif failure == "missing_action":
        actions.pop()
    elif failure == "provider":
        actions[0]["provider"] = "other.provider"
    elif failure == "access":
        actions[1]["access"] = "core"
    else:
        actions[0]["trusted_lines"] = ["store.create"]
    with pytest.raises(ValueError):
        validate_conversation_tool(tools)


def test_only_the_exact_published_version_composes(tools):
    assert resolve_conversation(attached(), tools=tools).source == "tool"
    tools.get_info("conversation").version = "1.1.0"
    composition = resolve_conversation(attached(), tools=tools)
    assert (composition.available, composition.reason) == (False, "tool_unpublished")
    tools.unregister("conversation")
    assert resolve_conversation(attached(), tools=tools).reason == "tool_unpublished"


# --- Strict configuration -------------------------------------------------------------------


def test_default_configuration_preserves_existing_conversation_bounds():
    config = ConversationToolConfig()
    assert config.model_dump() == {
        "modes": ["context", "llm"],
        "history": {"max_messages": 24, "max_characters": 32768},
        "activities": [],
    }


@pytest.mark.parametrize(
    "config",
    [
        {"modes": []},
        {"modes": ["context", "context"]},
        {"modes": ["voice"]},
        {"modes": "llm"},
        {"history": {"max_messages": 25}},
        {"history": {"max_characters": 32769}},
        {"history": {"max_messages": -1}},
        {"history": {"window": 3}},
        {"activities": [INTERVIEW, INTERVIEW]},
        {"activities": ["agent.creation_interview"]},
        {"activities": ["agent.creation_interview@1.0"]},
        {"activities": ["agent.creation_interview@latest"]},
        {"activities": [f"x.y@1.0.{n}" for n in range(9)]},
    ],
)
def test_configuration_is_strict_and_bounded(config):
    with pytest.raises(ValidationError):
        ConversationToolConfig.model_validate(config)


@pytest.mark.parametrize(
    "field",
    [
        "grants",
        "signals",
        "signal",
        "scope",
        "scopes",
        "trusted_lines",
        "provider",
        "prompt",
        "system_prompt",
        "instructions",
        "model",
        "llm_type",
        "temperature",
        "permit",
        "tenant_id",
        "agent_id",
        "enabled",
        "_hidden",
    ],
)
def test_configuration_cannot_express_authority_prompts_or_models(field):
    with pytest.raises(ValidationError):
        ConversationToolConfig.model_validate({field: "forged"})


def test_config_uses_the_existing_sparse_agent_reference_and_validator(tools):
    ref = AgentToolRef(key="conversation", config={"modes": ["context"]})
    issues = SimpleNamespace(add=Mock())
    config = AgentDefinitionValidator._tool_config(
        tools.get_info(ref.key), ref.config, "tools.0.config", issues
    )
    assert config == {"modes": ["context"]}
    issues.add.assert_not_called()
    for forged in ({"grants": ["chat.response"]}, {"modes": ["voice"]}):
        issues = SimpleNamespace(add=Mock())
        assert (
            AgentDefinitionValidator._tool_config(
                tools.get_info(ref.key), forged, "tools.0.config", issues
            )
            is None
        )
        issues.add.assert_called()


# --- Safe Catalog discovery -----------------------------------------------------------------

PRIVATE = ("chat.context", "chat.response", "tool.chat", "cognitive.chat", "src.domains")


def test_requester_catalog_exposes_safe_metadata_only(tools):
    service = RegistryService({"tool": tools})
    user = User(id="reader", tenant_id="tenant")
    raw = tools.get_info("conversation").metadata
    before = copy.deepcopy(raw)
    rows = service.list_for_client("tool", user, include_metadata=True)
    detail = service.get_info_for_client("tool", "conversation", user).metadata
    text = json.dumps([rows, detail])
    assert [r["key"] for r in rows] == ["conversation"]
    assert not [value for value in PRIVATE if value in text]
    assert not {"synaptic", "provider", "config", "execution_mode", "config_schema"} & set(detail)
    # Only presented knobs reveal schema: modes. History and activity contracts are set
    # by validated attachment (governed revision), not presented to requesters yet.
    schema = detail["agent_config_schema"]
    assert set(schema["properties"]) == {"modes"}
    assert schema["properties"]["modes"]["items"]["enum"] == ["context", "llm"]
    assert [o["value"] for o in detail["ui"]["admin"]["fields"]["modes"]["options"]] == [
        "context",
        "llm",
    ]
    assert raw == before
    assert service.list_for_client("tool", None) == []


@pytest.mark.asyncio
async def test_governed_catalog_entry_is_safe_and_not_an_authorization(tools):
    catalog = ResourceCatalog(
        registries={"tool": tools}, workflow_service=WorkflowService(FakeWorkflowDB([]))
    )
    entry = (await resolve(SimpleNamespace(catalog=catalog), "tool", "conversation")).entry
    text = entry.model_dump_json()
    assert not [value for value in (*PRIVATE, '"synaptic"', "execution_mode") if value in text]
    assert entry.details.tool_class == "conversational"
    assert entry.details.configuration.configurable
    assert entry.decisions.execution_authorization == "not_evaluated"


# --- V1 denial ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_v1_direct_execution_is_denied_even_when_attached():
    spec = AgentFactory()._ref_to_spec(AgentToolRef(key="conversation"))
    factories = {}
    prepare_factories(factories=factories, tool_anchor_map={}, tool_specs=[spec])
    with pytest.raises(ValueError, match="missing meta.route"):
        route_for_v1(factories, "conversation")
    tool = factories["conversation"]["cls"]()
    with pytest.raises(EventPermissionError, match="V1 is denied"):
        await tool.execute(text="hello", actor={"tenant_id": "forged"})
    with pytest.raises(EventPermissionError):
        await tool._run(text="hello")
    assert not {"_executor", "llm", "client_factory", "store"} & vars(ConversationTool()).keys()


# --- Registry-driven composition ------------------------------------------------------------


def test_explicit_attachment_composes_its_validated_configuration():
    composition = resolve_conversation(
        attached({"modes": ["context"], "history": {"max_messages": 4}})
    )
    assert (composition.source, composition.compatibility) == ("tool", None)
    assert composition.modes == {"context"}
    assert composition.require("context").history.max_messages == 4
    with pytest.raises(ConversationUnavailable) as denied:
        composition.require("llm")
    assert (denied.value.code, denied.value.reason) == (
        "composition_unavailable",
        "mode_not_allowed",
    )


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"runtime_version": "v1"}, "runtime_unsupported"),
        ({"runtime_version": None}, "runtime_unsupported"),
        ({"tools": "conversation"}, "definition_invalid"),
        ({"tools": [{"key": "conversation"}, {"key": "conversation"}]}, "attachment_duplicate"),
        ({"tools": [{"key": "conversation", "config": {"modes": ["voice"]}}]}, "config_invalid"),
        ({"tools": [{"key": "conversation", "config": {"grants": ["x"]}}]}, "config_invalid"),
        ({}, "not_attached"),  # chat/writer: an ordinary Agent without the Tool
        ({"type": "form", "role_name": "general"}, "not_attached"),
        ({"tools": [{"key": "reasoning"}]}, "not_attached"),
    ],
)
def test_without_a_valid_attachment_or_reviewed_entry_conversation_fails_closed(changes, reason):
    composition = resolve_conversation(definition(**changes))
    assert (composition.available, composition.reason) == (False, reason)
    for mode in ("context", "llm"):
        with pytest.raises(ConversationUnavailable):
            composition.require(mode)


def test_an_invalid_attachment_never_falls_back_to_compatibility():
    # Enki's own type/role is a compatibility entry, but explicit configuration wins.
    enki = attached({"modes": ["voice"]}, type="form", role_name="enki")
    composition = resolve_conversation(enki)
    assert (composition.source, composition.available) == ("tool", False)


LIVE = "caos-local"
ENKI_ID = "00000000-0000-4000-8000-000000000004"
THOTH_ID = "00000000-0000-4000-8000-000000000006"
NABU_ID = "697e8c4b-171a-b672-c90e-f4b6ca5dd05b"


def existing(agent_id, type_, role, tenant=LIVE, **changes):
    return definition(id=agent_id, tenant_id=tenant, type=type_, role_name=role, **changes)


def test_compatibility_is_an_allowlist_of_immutable_identities():
    # AGENT-DEF-SEC-0.1: exact (tenant, Agent ID) identities, each with its reviewed shape.
    assert [
        (e.id, e.tenant_id, e.agent_id, e.agent_type, e.role_name)
        for e in LEGACY_CONVERSATION_COMPATIBILITY
    ] == [
        ("legacy.v2.enki", LIVE, ENKI_ID, "form", "enki"),
        ("legacy.v2.thoth", LIVE, THOTH_ID, "chat", "thoth"),
        ("legacy.v2.nabu", LIVE, NABU_ID, "chat", "general"),
    ]
    # Enki and Thoth are exactly the policy-pinned Genesis identities.
    assert (ENKI_PROVISION.tenant_id, ENKI_PROVISION.agent_id) == (LIVE, ENKI_ID)
    assert (THOTH_PROVISION.tenant_id, THOTH_PROVISION.agent_id) == (LIVE, THOTH_ID)
    for agent_id, type_, role, entry, activities in (
        # MIG-FIX-0.1: exact, reviewed activities; only Enki's creation interview.
        (ENKI_ID, "form", "enki", "legacy.v2.enki", ["agent.creation_interview@1.0.0"]),
        (THOTH_ID, "chat", "thoth", "legacy.v2.thoth", []),
        (NABU_ID, "chat", "general", "legacy.v2.nabu", []),
    ):
        composition = resolve_conversation(existing(agent_id, type_, role))
        assert (composition.source, composition.compatibility) == ("legacy", entry)
        assert composition.require("llm").activities == activities


@pytest.mark.parametrize(
    "candidate",
    [
        # Newly created Agents selecting a matching Type/Role pair, without the Tool.
        definition(id="new-agent", tenant_id=LIVE, type="chat", role_name="general"),
        definition(id="new-agent", tenant_id=LIVE, type="chat", role_name=None),
        definition(id="new-thoth", tenant_id=LIVE, type="chat", role_name="thoth"),
        definition(id="new-enki", tenant_id=LIVE, type="form", role_name="enki"),
        definition(type="chat", role_name="general"),  # no identity at all (composition)
        # An allowlisted identity in another tenant, or with a changed shape.
        existing(THOTH_ID, "chat", "thoth", tenant="other-tenant"),
        existing(NABU_ID, "chat", "thoth"),
        existing(ENKI_ID, "chat", "general"),
        existing(THOTH_ID, "chat", "thoth", runtime_version="v1"),
    ],
)
def test_type_and_role_never_confer_conversation(candidate):
    composition = resolve_conversation(candidate)
    assert not composition.available and composition.source == "none"


def test_new_agents_converse_only_with_an_explicit_attachment():
    fresh = attached(id="new-agent", tenant_id=LIVE, role_name="general")
    assert resolve_conversation(fresh).source == "tool"


def test_resolution_is_deterministic_and_reads_nothing_else():
    payload = attached({"activities": [INTERVIEW]})
    frozen = copy.deepcopy(payload)
    results = [resolve_conversation(payload) for _ in range(3)]
    assert all(r == results[0] for r in results) and payload == frozen  # never mutates


# --- Activity Context integration boundary --------------------------------------------------


def contracts(*, frozen=True, owners=frozenset({"conversation.v2"})):
    resolvers = ResourceResolverRegistry()
    for kind in ("manuscript.session", "agent.creation_draft", "agent.draft.proposal"):
        resolvers.register(Resolver(kind))
    registry = ActivityContractRegistry(resolvers, owners)
    registry.register(dataclasses.replace(AGENT_CREATION_INTERVIEW, owner_kinds=owners))
    if frozen:
        registry.freeze()
    return registry


def test_declared_activity_contracts_resolve_against_the_frozen_registry():
    gate = ConversationGate(definitions=None)
    declares = attached({"activities": [INTERVIEW]})
    # Unbound: an activity declaration fails closed; none declared is unaffected.
    assert gate.resolve(declares).reason == "activity_unavailable"
    assert gate.resolve(attached()).available
    registry = contracts()
    gate.bind_activities(registry)
    gate.bind_activities(registry)  # idempotent for the same registry
    assert gate.resolve(declares).require("llm").activities == [INTERVIEW]
    for unknown in ("agent.creation_interview@1.0.1", "test.document_review@1.0.0"):
        assert gate.resolve(attached({"activities": [unknown]})).reason == "activity_unavailable"
    with pytest.raises(ValueError):
        gate.bind_activities(contracts())  # a different registry
    with pytest.raises(ValueError):
        ConversationGate(definitions=None).bind_activities(contracts(frozen=False))


def test_an_activity_for_another_owner_kind_is_not_conversational():
    resolvers = ResourceResolverRegistry()
    for kind in ("manuscript.session", "agent.creation_draft", "agent.draft.proposal"):
        resolvers.register(Resolver(kind))
    registry = ActivityContractRegistry(resolvers, frozenset({"conversation.v2", "test.thread"}))
    registry.register(
        dataclasses.replace(AGENT_CREATION_INTERVIEW, owner_kinds=frozenset({"test.thread"}))
    )
    registry.freeze()
    gate = ConversationGate(definitions=None)
    gate.bind_activities(registry)
    assert gate.resolve(attached({"activities": [INTERVIEW]})).reason == "activity_unavailable"


def test_activity_context_does_not_depend_on_the_conversation_tool():
    package = SRC / "domains" / "agentverse" / "activities"
    text = "\n".join(p.read_text() for p in package.glob("*.py"))
    assert "conversation_tool" not in text and "conversational_composition" not in text


# --- Runtime gate ---------------------------------------------------------------------------


class Definitions:
    """Read-only fake: any call other than ``find_by_id`` fails the test."""

    def __init__(self, document):
        self.document = document
        self.find_by_id = AsyncMock(side_effect=lambda agent_id: copy.deepcopy(self.document))

    def __getattr__(self, name):
        raise AssertionError(f"Conversation composition must not call {name}")


@pytest.mark.asyncio
async def test_the_runtime_gate_reads_one_definition_and_writes_nothing():
    document = {**attached({"modes": ["context"]}), "id": "agent", "tenant_id": "tenant"}
    gate = ConversationGate(definitions=Definitions(document))
    assert (await gate.require("tenant", "agent", "context")).modes == ["context"]
    with pytest.raises(ConversationUnavailable):
        await gate.require("tenant", "agent", "llm")
    for changes in ({"runtime_version": "v1"}, {"tenant_id": "other"}):
        gate = ConversationGate(definitions=Definitions({**document, **changes}))
        with pytest.raises(EventPermissionError):
            await gate.require("tenant", "agent", "context")
    gate = ConversationGate(definitions=Definitions(None))
    with pytest.raises(EventPermissionError):
        await gate.require("tenant", "agent", "context")


# --- Former hardcoded gate call sites -------------------------------------------------------


def test_the_hardcoded_type_role_gate_is_gone():
    sources = (SRC / "domains").rglob("*.py")
    offenders = [
        str(p)
        for p in sources
        if "tests" not in p.parts and "supports_v2_conversation" in p.read_text()
    ]
    assert offenders == []


def test_default_presentation_follows_composition_not_type():
    assert [c.key for c in effective_gui_components(definition())] == []
    assert [(c.key, c.default) for c in effective_gui_components(attached())] == [
        ("conversation", True)
    ]
    # Presentation of a reviewed creation composition (never conversation availability).
    enki = definition(type="form", role_name="enki")
    assert [c.key for c in effective_gui_components(enki)] == [
        "agent_configuration",
        "conversation",
    ]
    assert not resolve_conversation(enki).available
    # An invalid explicit attachment removes the default Conversation component.
    broken = attached({"modes": ["voice"]}, type="form", role_name="enki")
    assert [c.key for c in effective_gui_components(broken)] == ["agent_configuration"]


def creation(**changes):
    return AgentCreationInput.model_validate(
        {
            "name": "Writer",
            "type": "chat",
            "role_name": "writer",
            "personality_key": "empty_eva",
            "prompt": "Write.",
            "gui_components": [{"type": "chat", "key": "conversation", "default": True}],
            **changes,
        }
    )


def test_creation_validation_requires_composition_for_a_conversation_component():
    issues = SimpleNamespace(add=Mock())
    AgentDefinitionValidator._gui(creation(), None, None, [], issues)
    issues.add.assert_called_once()
    assert issues.add.call_args.args[1] == "gui.component_incompatible"
    issues = SimpleNamespace(add=Mock())
    AgentDefinitionValidator._gui(creation(tools=[{"key": "conversation"}]), None, None, [], issues)
    issues.add.assert_not_called()


# --- Nataraja diagnostic --------------------------------------------------------------------


def test_nataraja_reports_implicit_compatibility_safely(local_env, manifest):  # noqa: F811
    explicit = {
        **attached({"modes": ["context"]}),
        "id": "explicit",
        "name": "Explicit",
        "tenant_id": "caos-local",
    }
    invalid = {**attached({"modes": ["voice"]}), "id": "invalid", "name": "Invalid"}
    plain = {**definition(), "id": "plain", "name": "Plain"}  # chat/writer: nothing presented
    presented = {**definition(role_name="general"), "id": "fresh", "name": "Fresh"}
    observation = baseline(local_env, agents=[enki_document(), explicit, invalid, plain, presented])
    report = evaluate(manifest, observation, mode="inspect")
    rows = {r["id"]: r for r in report["resources"] if r["kind"] == "conversation"}
    assert rows[f"conversation:{ENKI}"] == {
        "kind": "conversation",
        "id": f"conversation:{ENKI}",
        "name": "Enki",
        "status": "passed",
        "source": "legacy",
        "implicit": True,
        "compatibility": "legacy.v2.enki",
        "modes": ["context", "llm"],
        "activities": ["agent.creation_interview@1.0.0"],
        "retire_by": "governed definition revision attaching conversation@1.0.0",
    }
    assert rows["conversation:explicit"]["source"] == "tool"
    assert rows["conversation:explicit"]["implicit"] is False
    assert rows["conversation:invalid"]["status"] == "warning"
    assert "conversation:plain" not in rows  # A non-conversational Agent is not a finding.
    # A new chat/general Agent presents Conversation but is not composed (no allowlist).
    assert (rows["conversation:fresh"]["status"], rows["conversation:fresh"]["source"]) == (
        "warning",
        "none",
    )
    assert "conversation.presented_without_composition" in {f["code"] for f in report["findings"]}
    finding = next(f for f in report["findings"] if f["code"] == "conversation.attachment_invalid")
    assert finding["reason"] == "config_invalid"
    text = json.dumps([rows, finding])
    assert not [value for value in (*PRIVATE, "grant", "scope") if value in text]


def test_no_new_capability_signals_or_grants():
    actions = {a.signal for a in conversation_tool.synaptic.capabilities}
    assert actions == {"chat.context", "chat.response"}
    policy = json.loads((SRC.parent / "config" / "genesis.local.json").read_text())
    granted = {signal for grant in policy["grants"] for signal in grant["invoke"]}
    assert not {s for s in granted if s.startswith("conversation")}
    # Nabu (a live, ungranted chat/general Agent) holds no grant in the reviewed policy.
    nabu = "697e8c4b-171a-b672-c90e-f4b6ca5dd05b"
    assert nabu not in {grant["agent_id"] for grant in policy["grants"]}
