"""``data.validation`` through real CPBX/UTU, the validator container and catalog policy."""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock, Mock

import pytest
from src.base.entities.user import User
from dependency_injector import providers
from pydantic import BaseModel, Field

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import (
    AgentPrincipalResolver,
)
from src.domains.agentverse.ncsc.authorization import SynapticAuthorization
from src.domains.agentverse.ncsc.cpbx import SynapticExchange
from src.domains.agentverse.ncsc.models import Synapse, SynapticGrants, TrustedLine
from src.domains.agentverse.registries.base import Registry
from src.domains.agentverse.registries.synaptic_registry import SynapticRegistry
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.security.revocation import ExecutionPermit, policy_version
from src.domains.agentverse.security.utu import AuthorizedAgentPrincipal, UTUAuthority
from src.domains.agentverse.services.workflow_service import WorkflowService
from src.domains.agentverse.tools.validation_tool import (
    DataValidationTool,
    register_data_validation,
)
from src.domains.agentverse.tools.validation_tool.dependencies.di_container import (
    ValidationToolContainer,
)
from src.domains.agentverse.tools.validation_tool.spec import (
    DATA_VALIDATION_PROVIDER,
    DATA_VALIDATION_SIGNAL,
    DATA_VALIDATION_VERSION,
)

TENANT, OTHER_TENANT = "tenant-a", "tenant-b"
REQUESTER, CALLER = "owner-1", "genesis-caller"
KEY = AgentInstanceKey(TENANT, REQUESTER, CALLER, "secret-session-id")
KINDS = ("agent", "role", "personality", "rulepack", "tool", "workflow")


class ProbeConfig(BaseModel):
    temperature: float = Field(default=0.3, ge=0, le=2)
    history_window: int = Field(default=12, ge=0)


class LlmProbeTool:
    required_dependencies: ClassVar[list[str]] = ["llm"]


# --- Catalog -------------------------------------------------------------------------


def put(
    registries: dict[str, Registry[Any]],
    kind: str,
    key: str,
    component: Any = object,
    **metadata: Any,
) -> None:
    registries[kind].register(
        name=key, key=key, component=component, metadata={"ownership": "global", **metadata}
    )


def catalog() -> dict[str, Registry[Any]]:
    """Isolated catalog: global built-ins plus foreign-tenant and private entries."""
    r: dict[str, Registry[Any]] = {kind: Registry(kind) for kind in KINDS}
    put(r, "agent", "chat", agent_group="interactive_ui", agent_subclass="conversational")
    put(r, "agent", "form", agent_group="interactive_ui", agent_subclass="operational")
    put(r, "role", "general", agent_group="interactive_ui", agent_subclass="conversational")
    put(
        r,
        "role",
        "writer",
        agent_group="interactive_ui",
        agent_subclass="conversational",
        capabilities={"required_tools": {"probe": "Probe"}},
    )
    put(r, "role", "foreign_role", ownership="tenant", tenant_id=OTHER_TENANT)
    put(r, "personality", "empty_eva")
    put(r, "personality", "foreign_soul", ownership="tenant", tenant_id=OTHER_TENANT)
    put(r, "rulepack", "gdpr")
    put(r, "rulepack", "green")
    put(r, "rulepack", "foreign_pack", ownership="tenant", tenant_id=OTHER_TENANT)
    schema = f"{__name__}.ProbeConfig"
    put(r, "tool", "probe", config_schema=schema, config=ProbeConfig().model_dump())
    put(r, "tool", "plain")  # no configuration contract
    put(r, "tool", "bare_schema", config_schema="ProbeConfig")  # unresolvable, like whisperer
    put(r, "tool", "llm_probe", component=LlmProbeTool, config_schema=schema)
    put(r, "tool", "audited", required_rulepacks=True, min_rulepacks=2)
    put(r, "tool", "core", ownership="system", exposure="internal")
    put(r, "tool", "foreign_tool", ownership="tenant", tenant_id=OTHER_TENANT)
    put(r, "tool", "tenant_tool", ownership="tenant", tenant_id=TENANT)
    put(
        r,
        "tool",
        "others_private",
        ownership="tenant",
        tenant_id=TENANT,
        visibility="private",
        creator="someone-else",
    )
    put(
        r,
        "tool",
        "own_private",
        ownership="tenant",
        tenant_id=TENANT,
        visibility="private",
        creator=REQUESTER,
    )
    return r


def workflow(
    key: str, *, tools: list[str], required: list[str] | None = None, **owner: Any
) -> dict[str, Any]:
    return {
        "key": key,
        "name": key,
        "version": "1.2.0",
        **({"ownership": "global"} | owner),
        "definition": {"steps": [{"id": t, "type": "tool", "command": f"@{t}"} for t in tools]},
        "required_tools": tools if required is None else required,
    }


class FakeWorkflowDB:
    """Read-only catalog. Any write attribute access fails the test."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = {row["key"]: row for row in rows}
        self.reads: list[str] = []

    async def find_one(self, query: dict[str, Any], **_: Any) -> dict[str, Any] | None:
        self.reads.append("find_one")
        return copy.deepcopy(self.rows.get(query["key"]))

    async def find_all(self, query: dict[str, Any] | None = None, **_: Any) -> list[dict[str, Any]]:
        self.reads.append("find_all")
        keys = (query or {}).get("key", {}).get("$in", list(self.rows))
        return [copy.deepcopy(self.rows[k]) for k in keys if k in self.rows]

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"data.validation must not call {name}")


def workflow_rows() -> list[dict[str, Any]]:
    return [
        workflow("flow", tools=["plain"]),
        workflow("audited_flow", tools=["audited"]),
        # A stale stored tool set: readable steps, but a required tool since removed.
        workflow("ghost_flow", tools=["plain"], required=["plain", "unregistered"]),
        workflow("foreign_flow", tools=["plain"], ownership="tenant", tenant_id=OTHER_TENANT),
    ]


# --- Production composition ----------------------------------------------------------


def authority(
    *, grants: set[str], trusted_lines: tuple[TrustedLine, ...] = (), stale: bool = False
) -> tuple[UTUAuthority, tuple[str, ...]]:
    policy = SynapticAuthorization(
        grants={(TENANT, CALLER): SynapticGrants(frozenset(grants))},
        trusted_lines=trusted_lines,
    )
    current = (policy_version(policy), "incarnation", "0", "0")
    resolver = AgentPrincipalResolver(
        definitions=SimpleNamespace(find_by_id=AsyncMock()),
        sessions=SimpleNamespace(
            get_session=AsyncMock(), bind_revocation_handler=Mock(),
            resource_requester=AsyncMock(return_value=User(id=KEY.user_id, tenant_id=TENANT)),
        ),
        authorization=policy,
        approvals={},
    )
    revocations = SimpleNamespace(
        read=AsyncMock(return_value=("old", "incarnation", "0", "0") if stale else current)
    )
    return UTUAuthority(resolver=resolver, authorization=policy, revocations=revocations), current


@pytest.fixture
def stack(monkeypatch: pytest.MonkeyPatch):
    def build(
        *, grants: set[str] = frozenset({DATA_VALIDATION_SIGNAL}), **kwargs: Any
    ) -> SimpleNamespace:
        from src.domains.agentverse.registries.registries import registries as global_registries

        registries = catalog()
        # WorkflowService's existing read policy consults the global catalog.
        for kind, registry in registries.items():
            monkeypatch.setitem(global_registries, kind, registry)
        utu, versions = authority(grants=set(grants), **kwargs)
        db = FakeWorkflowDB(workflow_rows())
        container = ValidationToolContainer(
            registries=providers.Object(registries),
            workflow_service=providers.Object(WorkflowService(db)),
            utu_authority=providers.Object(utu),
        )
        registry = SynapticRegistry()
        binding = register_data_validation(registry, container.tool())
        exchange = SynapticExchange(registry=registry, authorization=utu)
        client = exchange.bind(
            AuthorizedAgentPrincipal(CALLER, TENANT, ExecutionPermit(KEY, versions))
        )
        return SimpleNamespace(
            client=client,
            exchange=exchange,
            registry=registry,
            binding=binding,
            container=container,
            db=db,
            registries=registries,
            versions=versions,
        )

    return build


def creation(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "name": "Enki",
        "type": "chat",
        "role_name": "writer",
        "personality_key": "empty_eva",
        "prompt": "You are Enki.",
        "tools": [{"key": "probe", "config": {"temperature": 0.7}}],
        "workflows": ["flow"],
        "rule_pack_ids": ["gdpr"],
    }
    value.update(overrides)
    return value


async def validate(
    s: SimpleNamespace, payload: Any, resource_type: Any = "agent"
) -> dict[str, Any]:
    envelope = await s.client.request(
        DATA_VALIDATION_SIGNAL, {"resource_type": resource_type, "payload": payload}
    )
    assert envelope["success"] is True and envelope["error"] is None
    return envelope["result"]


def codes(result: dict[str, Any]) -> set[tuple[str, str]]:
    return {(issue["path"], issue["code"]) for issue in result["issues"]}


# --- Valid input and deterministic normalization ------------------------------------


@pytest.mark.asyncio
async def test_valid_creation_input_returns_normalized_definition_and_references(stack) -> None:
    s = stack()
    result = await validate(s, creation())
    assert result["valid"] is True and result["issues"] == []
    assert result["resource_type"] == "agent"
    assert result["normalized"] == {
        "name": "Enki",
        "type": "chat",
        "role_name": "writer",
        "personality_key": "empty_eva",
        "prompt": "You are Enki.",
        "visibility": "private",
        "whitelisted_groups": [],
        # Sparse: only the agent's override, never a snapshot of registry defaults.
        "tools": [{"key": "probe", "config": {"temperature": 0.7}}],
        "workflows": ["flow"],
        "rule_pack_ids": ["gdpr"],
    }
    resolved = result["resolved"]
    assert resolved["role"] == {"key": "writer", "version": "1.0.0", "requires_tools": True}
    assert resolved["workflows"] == [
        {"key": "flow", "version": "1.2.0", "required_tools": ["plain"]}
    ]
    assert resolved["workflow_tools"] == ["plain"]
    assert len(result["normalized_digest"]) == 64
    # Nothing server-owned or persistence-related is generated.
    for field in (
        "id",
        "chat_url",
        "dna_sequence",
        "system_name",
        "ownership",
        "tenant_id",
        "creator",
        "exposure",
        "runtime_version",
    ):
        assert field not in result["normalized"]


@pytest.mark.asyncio
async def test_equivalent_inputs_normalize_to_identical_output_and_digest(stack) -> None:
    s = stack()
    first = await validate(s, creation())
    second = await validate(
        s,
        creation(
            name="  Enki ",
            type="Chat",
            role_name="Writer",
            personality_key="EMPTY_EVA",
            tools=[{"key": "Probe", "config": {"temperature": "0.7"}}],
            workflows=["FLOW"],
            rule_pack_ids=["GDPR"],
        ),
    )
    assert first["normalized"] == second["normalized"]
    assert first["normalized_digest"] == second["normalized_digest"]
    assert first == await validate(s, creation())


@pytest.mark.asyncio
async def test_digest_binds_the_normalized_input_to_the_verified_requester(stack) -> None:
    from src.domains.agentverse.tools.validation_tool.models import validation_digest

    result = await validate(stack(), creation())
    assert result["normalized_digest"] == validation_digest(
        "agent", result["normalized"], tenant_id=TENANT, subject_id=REQUESTER
    )
    assert result["normalized_digest"] != validation_digest(
        "agent", result["normalized"], tenant_id=TENANT, subject_id="someone-else"
    )


@pytest.mark.asyncio
async def test_backend_kinds_required_by_tools_are_reported_for_composition(stack) -> None:
    s = stack()
    result = await validate(s, creation(tools=[{"key": "probe"}, {"key": "llm_probe"}]))
    assert result["valid"] and result["resolved"]["required_backend_kinds"] == ["llm"]


# --- Structure: missing, malformed, unknown and forbidden fields --------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["name", "type", "role_name", "personality_key", "prompt"])
async def test_missing_required_fields_are_reported(stack, field: str) -> None:
    payload = creation()
    del payload[field]
    result = await validate(stack(), payload)
    assert not result["valid"] and codes(result) == {(field, "field.required")}
    assert result["normalized"] is None and result["resolved"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("override", "path"),
    [
        ({"name": "-bad-"}, "name"),
        ({"name": "admin"}, "name"),
        ({"prompt": "   "}, "prompt"),
        ({"visibility": "secret"}, "visibility"),
        ({"tools": "probe"}, "tools"),
        ({"tools": [{"key": "probe", "config": "fast"}]}, "tools.0.config"),
        ({"tools": [{"config": {}}]}, "tools.0.key"),
        ({"workflows": [""]}, "workflows.0"),
    ],
)
async def test_malformed_fields_are_reported(stack, override: dict[str, Any], path: str) -> None:
    result = await validate(stack(), creation(**override))
    assert not result["valid"]
    assert {p for p, _ in codes(result)} == {path}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "id",
        "creator",
        "user_id",
        "ownership",
        "tenant_id",
        "exposure",
        "runtime_version",
        "genesis_local_seed",
        "system_name",
        "chat_url",
        "dna_sequence",
    ],
)
async def test_server_controlled_fields_are_rejected(stack, field: str) -> None:
    result = await validate(stack(), creation(**{field: "spoofed"}))
    assert not result["valid"] and codes(result) == {(field, "field.server_controlled")}


@pytest.mark.asyncio
async def test_unknown_fields_are_rejected_not_dropped(stack) -> None:
    result = await validate(
        stack(),
        creation(
            llm_type="openai", role={"name": "writer"}, tools=[{"key": "probe", "scope": "system"}]
        ),
    )
    assert codes(result) == {
        ("llm_type", "field.unknown"),
        ("role", "field.unknown"),
        ("tools.0.scope", "field.unknown"),
    }


@pytest.mark.asyncio
async def test_messages_never_echo_submitted_values(stack) -> None:
    result = await validate(stack(), creation(name="SENTINEL-<bad>", tenant_id="SENTINEL"))
    assert "SENTINEL" not in repr(result)


# --- References -----------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("override", "path"),
    [
        ({"type": "robot"}, "type"),
        ({"role_name": "unknown_role"}, "role_name"),
        ({"personality_key": "missing_soul"}, "personality_key"),
        ({"rule_pack_ids": ["gdpr", "nope"]}, "rule_pack_ids.1"),
        ({"tools": [{"key": "probe"}, {"key": "hammer"}]}, "tools.1.key"),
        ({"workflows": ["flow", "missing_flow"]}, "workflows.1"),
    ],
)
async def test_unknown_references_fail_closed(stack, override: dict[str, Any], path: str) -> None:
    result = await validate(stack(), creation(**override))
    assert not result["valid"] and codes(result) == {(path, "reference.unavailable")}


@pytest.mark.asyncio
async def test_duplicate_references_are_rejected(stack) -> None:
    result = await validate(
        stack(),
        creation(
            tools=[{"key": "probe"}, {"key": "PROBE"}],
            rule_pack_ids=["gdpr", "gdpr"],
            workflows=["flow", "Flow"],
        ),
    )
    assert codes(result) == {
        ("tools.1.key", "reference.duplicate"),
        ("rule_pack_ids.1", "reference.duplicate"),
        ("workflows.1", "reference.duplicate"),
    }


@pytest.mark.asyncio
async def test_system_and_internal_tools_are_not_client_attachable(stack) -> None:
    # Core/system tools are server-composed; an explicit attachment would otherwise
    # downgrade their system scope (V1 merge behavior).
    result = await validate(stack(), creation(tools=[{"key": "probe"}, {"key": "core"}]))
    assert codes(result) == {("tools.1.key", "reference.unavailable")}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("override", "path"),
    [
        ({"role_name": "foreign_role"}, "role_name"),
        ({"personality_key": "foreign_soul"}, "personality_key"),
        ({"rule_pack_ids": ["foreign_pack"]}, "rule_pack_ids.0"),
        ({"tools": [{"key": "probe"}, {"key": "foreign_tool"}]}, "tools.1.key"),
        ({"tools": [{"key": "probe"}, {"key": "others_private"}]}, "tools.1.key"),
        ({"workflows": ["foreign_flow"]}, "workflows.0"),
    ],
)
async def test_cross_tenant_and_private_references_are_indistinguishable_from_unknown(
    stack, override: dict[str, Any], path: str
) -> None:
    result = await validate(stack(), creation(**override))
    (issue,) = result["issues"]
    assert (issue["path"], issue["code"]) == (path, "reference.unavailable")
    unknown_kind = issue["message"]
    assert "foreign" not in unknown_kind and "private" not in unknown_kind


@pytest.mark.asyncio
async def test_own_tenant_and_own_private_resources_are_readable(stack) -> None:
    result = await validate(
        stack(),
        creation(tools=[{"key": "probe"}, {"key": "tenant_tool"}, {"key": "own_private"}]),
    )
    assert result["valid"], result["issues"]


@pytest.mark.asyncio
async def test_group_whitelisting_requires_verified_membership(stack) -> None:
    # The permit carries no roles, so no group membership is inferred (fail closed).
    result = await validate(stack(), creation(whitelisted_groups=["team-a"]))
    assert codes(result) == {("whitelisted_groups", "visibility.group_not_permitted")}


# --- Tool configuration ----------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({"temperature": 9}, {("tools.0.config.temperature", "tool.config_invalid")}),
        ({"temperature": "hot"}, {("tools.0.config.temperature", "tool.config_invalid")}),
        ({"bogus": 1}, {("tools.0.config.bogus", "tool.config_unknown_field")}),
        ({"_hidden": True}, {("tools.0.config._hidden", "tool.config_unknown_field")}),
    ],
)
async def test_malformed_tool_configuration_is_rejected(
    stack, config: dict[str, Any], expected: set[tuple[str, str]]
) -> None:
    result = await validate(stack(), creation(tools=[{"key": "probe", "config": config}]))
    assert not result["valid"] and codes(result) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["plain", "bare_schema"])
async def test_configuration_without_a_resolvable_contract_fails_closed(stack, tool: str) -> None:
    payload = creation(tools=[{"key": "probe"}, {"key": tool, "config": {"temperature": 0.1}}])
    result = await validate(stack(), payload)
    assert codes(result) == {("tools.1.config", "tool.config_schema_unavailable")}
    # Without overrides, a contract is not needed.
    assert (await validate(stack(), creation(tools=[{"key": "probe"}, {"key": tool}])))["valid"]


@pytest.mark.asyncio
async def test_real_tool_contract_is_enforced(stack, monkeypatch: pytest.MonkeyPatch) -> None:
    s = stack()
    put(
        s.registries,
        "tool",
        "chat_v2_probe",
        config_schema="src.domains.agentverse.tools.chat_tool_v2.ChatToolV2Config",
    )
    tools = [{"key": "probe"}, {"key": "chat_v2_probe", "config": {"top_p": 0}}]
    result = await validate(s, creation(tools=tools))
    assert codes(result) == {("tools.1.config.top_p", "tool.config_invalid")}


# --- Composition compatibility ------------------------------------------------------


@pytest.mark.asyncio
async def test_role_required_tools_must_be_attached(stack) -> None:
    result = await validate(stack(), creation(tools=[{"key": "plain"}]))
    assert codes(result) == {("tools", "composition.required_tool_missing")}


@pytest.mark.asyncio
async def test_role_and_agent_type_must_declare_compatible_groups(stack) -> None:
    result = await validate(stack(), creation(type="form"))
    assert codes(result) == {("role_name", "composition.role_type_mismatch")}


@pytest.mark.asyncio
async def test_tool_rulepack_minimums_apply_to_attached_and_workflow_tools(stack) -> None:
    attached = await validate(stack(), creation(tools=[{"key": "probe"}, {"key": "audited"}]))
    assert codes(attached) == {("rule_pack_ids", "composition.rulepacks_insufficient")}
    via_workflow = await validate(stack(), creation(workflows=["audited_flow"]))
    assert codes(via_workflow) == {("rule_pack_ids", "composition.rulepacks_insufficient")}
    enough = await validate(
        stack(), creation(workflows=["audited_flow"], rule_pack_ids=["gdpr", "green"])
    )
    assert enough["valid"]


@pytest.mark.asyncio
async def test_workflow_requiring_unregistered_tool_is_rejected(stack) -> None:
    # The existing workflow read policy already walks the stored ``required_tools``,
    # so a stale tool set fails there first; the composition check is defense in depth.
    result = await validate(stack(), creation(workflows=["ghost_flow"]))
    assert codes(result) == {("workflows.0", "reference.unavailable")}


@pytest.mark.asyncio
async def test_workflow_whose_steps_use_unreadable_tools_is_unavailable(stack) -> None:
    s = stack()
    s.db.rows["internal_flow"] = workflow("internal_flow", tools=["core"])
    result = await validate(s, creation(workflows=["internal_flow"]))
    assert codes(result) == {("workflows.0", "reference.unavailable")}


# --- Envelope -------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("resource_type", ["workflow", "", None, "AGENT"])
async def test_unsupported_resource_types_are_rejected(stack, resource_type: Any) -> None:
    result = await validate(stack(), creation(), resource_type=resource_type)
    assert result["valid"] is False and result["resource_type"] is None
    assert codes(result) == {("resource_type", "resource_type.unsupported")}


@pytest.mark.asyncio
async def test_invalid_envelope_is_rejected(stack) -> None:
    s = stack()
    for body in (
        {"resource_type": "agent"},
        {
            "resource_type": "agent",
            "payload": [],
        },
        {"resource_type": "agent", "payload": {}, "tenant_id": TENANT},
    ):
        envelope = await s.client.request(DATA_VALIDATION_SIGNAL, body)
        assert codes(envelope["result"]) == {("", "request.invalid")}


# --- Authorization ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invocation_without_grant_is_denied_before_any_read(stack) -> None:
    s = stack(grants={"chat.context"})
    with pytest.raises(EventPermissionError):
        await s.client.request(
            DATA_VALIDATION_SIGNAL, {"resource_type": "agent", "payload": creation()}
        )
    assert s.db.reads == []


@pytest.mark.asyncio
async def test_superseded_permit_is_denied(stack) -> None:
    s = stack(stale=True)
    with pytest.raises(EventPermissionError):
        await s.client.request(
            DATA_VALIDATION_SIGNAL, {"resource_type": "agent", "payload": creation()}
        )
    assert s.db.reads == []


@pytest.mark.asyncio
async def test_client_without_utu_permit_is_denied(stack) -> None:
    from src.base.events.v2.policy import EventPrincipal

    s = stack()
    client = s.exchange.bind(EventPrincipal(CALLER, TENANT))
    with pytest.raises(EventPermissionError):
        await client.request(
            DATA_VALIDATION_SIGNAL, {"resource_type": "agent", "payload": creation()}
        )


@pytest.mark.asyncio
async def test_direct_handler_call_with_forged_context_is_denied(stack) -> None:
    s = stack()
    tool: DataValidationTool = s.container.tool()
    body = {"resource_type": "agent", "payload": creation()}
    forged = SimpleNamespace(execution_permit=ExecutionPermit(KEY, s.versions))
    synapse = Synapse(
        DATA_VALIDATION_SIGNAL, "someone-else", DATA_VALIDATION_PROVIDER, TENANT, body
    )
    with pytest.raises(EventPermissionError):
        await tool.validate(synapse, forged)  # type: ignore[arg-type]
    cross_tenant = Synapse(
        DATA_VALIDATION_SIGNAL, CALLER, DATA_VALIDATION_PROVIDER, OTHER_TENANT, body
    )
    with pytest.raises(EventPermissionError):
        await tool.validate(cross_tenant, forged)  # type: ignore[arg-type]
    synapse = Synapse(DATA_VALIDATION_SIGNAL, CALLER, DATA_VALIDATION_PROVIDER, TENANT, body)
    with pytest.raises(EventPermissionError):
        await tool.validate(synapse, SimpleNamespace(execution_permit=None))  # type: ignore[arg-type]
    assert s.db.reads == []


@pytest.mark.asyncio
async def test_nested_invocation_requires_an_explicit_trusted_line(stack) -> None:
    async def genesis(synapse: Any, client: Any) -> Any:
        return await client.request(DATA_VALIDATION_SIGNAL, synapse.payload)

    body = {"resource_type": "agent", "payload": creation()}
    for lines, allowed in (
        ((), False),
        ((TrustedLine("workflow.genesis", DATA_VALIDATION_SIGNAL),), True),
    ):
        s = stack(grants={"workflow.genesis"}, trusted_lines=lines)
        s.registry.subscribe("workflow.genesis", provider="workflow.test")(genesis)
        if allowed:
            assert (await s.client.request("workflow.genesis", body))["result"]["valid"]
        else:
            with pytest.raises(EventPermissionError):
                await s.client.request("workflow.genesis", body)


@pytest.mark.asyncio
async def test_payload_identity_never_selects_the_requester(stack) -> None:
    # Another tenant's resources stay unreadable even if the payload names that tenant.
    payload = creation(role_name="foreign_role")
    result = await validate(stack(), payload)
    assert ("role_name", "reference.unavailable") in codes(result)


# --- No side effects ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validation_has_no_persistence_or_runtime_side_effects(
    stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.domains.agentverse.agents.factory import AgentFactory
    from src.domains.agentverse.tools.tool_manager import ToolManager

    def forbidden(*_: Any, **__: Any) -> None:
        raise AssertionError("data.validation must not construct runtime agents or tools")

    monkeypatch.setattr(AgentFactory, "__init__", forbidden)
    monkeypatch.setattr(ToolManager, "__init__", forbidden)
    s = stack()
    before = {kind: dict(reg._items) for kind, reg in s.registries.items()}
    payload = creation(tools=[{"key": "probe", "config": {"temperature": "0.7"}}])
    snapshot = copy.deepcopy(payload)
    assert (await validate(s, payload))["valid"]
    assert payload == snapshot  # input is not mutated in place
    assert set(s.db.reads) <= {"find_one", "find_all"}  # FakeWorkflowDB rejects anything else
    assert {kind: dict(reg._items) for kind, reg in s.registries.items()} == before
    assert DATA_VALIDATION_SIGNAL not in s.registries["tool"]


# --- Registration -----------------------------------------------------------------------


def test_registration_is_exact_versioned_system_only_and_grants_nothing(stack) -> None:
    s = stack(grants=set())
    binding = s.registry.resolve(DATA_VALIDATION_SIGNAL, DATA_VALIDATION_VERSION)
    assert (binding.signal, binding.provider, binding.version) == (
        DATA_VALIDATION_SIGNAL,
        DATA_VALIDATION_PROVIDER,
        DATA_VALIDATION_VERSION,
    )
    info = s.registry.get_info(DATA_VALIDATION_SIGNAL)
    assert info is not None
    assert (info.metadata["access"], info.metadata["side_effects"]) == ("system", False)


def test_capability_is_absent_from_the_v1_tool_catalog() -> None:
    from src.domains.agentverse.registries import tool_registry_instance

    for key in ("data.validation", "data_validation", "datavalidation", "validation"):
        assert key not in tool_registry_instance


def test_agentverse_container_wires_shared_catalog_workflows_and_utu() -> None:
    from src.domains.agentverse.dependencies.di_container import AgentverseContainer
    from src.domains.agentverse.registries.registries import registries

    container = AgentverseContainer()
    container.mongodb_repository.override(providers.Object(Mock()))
    container.redis_repository.override(providers.Object(Mock()))
    validation = container.validation()
    tool = validation.tool()
    assert tool is validation.tool()
    assert tool._authority is container.utu_authority()
    agent_validator = validation.agent_validator()
    assert agent_validator._registries is registries
    assert isinstance(agent_validator._workflows, WorkflowService)
    with pytest.raises(TypeError):
        validation.validators()["workflow"] = agent_validator  # type: ignore[index]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("grants", "lines", "registered"),
    [
        (set(), (), False),
        ({"chat.context"}, (), False),
        ({DATA_VALIDATION_SIGNAL}, (), True),
        ({"workflow.genesis"}, (TrustedLine("workflow.genesis", DATA_VALIDATION_SIGNAL),), True),
    ],
)
async def test_bootstrap_registers_data_validation_only_for_reviewed_policy(
    monkeypatch: pytest.MonkeyPatch,
    stack,
    grants: set[str],
    lines: tuple[Any, ...],
    registered: bool,
) -> None:
    from dataclasses import replace

    from fastapi import FastAPI

    from src.base.services.jwt_service import JWTService
    from src.domains.agentverse import registries
    from src.domains.agentverse.dependencies.genesis_bootstrap import (
        build_genesis_security,
        configure_genesis_transport,
    )

    monkeypatch.delenv("GENESIS_LOCAL_ENABLED", raising=False)
    monkeypatch.delenv("GENESIS_SECURITY_POLICY_FILE", raising=False)
    monkeypatch.setenv("GENESIS_SESSIONS_ENABLED", "true")
    registry = SynapticRegistry()
    monkeypatch.setattr(registries, "synaptic_registry_instance", registry)
    container = SimpleNamespace(
        redis=SimpleNamespace(
            redis_repository=AsyncMock(return_value=SimpleNamespace(eval=AsyncMock()))
        ),
        services=SimpleNamespace(jwt_service=lambda: JWTService()),
    )
    bindings = replace(
        build_genesis_security(container)["genesis_security"],
        authorization=SynapticAuthorization(
            grants={(TENANT, CALLER): SynapticGrants(frozenset(grants))}, trusted_lines=lines
        ),
    )
    tool = stack().container.tool()
    app = FastAPI()
    app.container = SimpleNamespace(  # type: ignore[attr-defined]
        agentverse=SimpleNamespace(
            utu_authority=Mock(
                side_effect=lambda: bindings.sessions.bind_revocation_handler(AsyncMock())
            ),
            validation=lambda: SimpleNamespace(tool=lambda: tool),
        )
    )
    configure_genesis_transport(app, bindings)
    await app.state.start_genesis_sessions()
    await app.state.close_genesis_sessions()
    assert (DATA_VALIDATION_SIGNAL in registry) is registered
    if registered:
        assert (
            registry.resolve(DATA_VALIDATION_SIGNAL, DATA_VALIDATION_VERSION).handler
            == tool.validate
        )


# --- GUI composition contract v1 --------------------------------------------------------

CHAT_GUI = {"type": "chat", "key": "conversation", "default": True}


def form_gui(source: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "type": "form",
        "key": "settings",
        "default": True,
        "config": {"source": source},
        **extra,
    }


def gui_creation(**overrides: Any) -> dict[str, Any]:
    return creation(role_name="general", tools=[], **overrides)


@pytest.mark.asyncio
async def test_gui_is_optional_and_absent_gui_keeps_the_normalized_digest(stack) -> None:
    s = stack()
    without = await validate(s, gui_creation())
    assert without["valid"] and "gui_components" not in without["normalized"]
    declared = await validate(s, gui_creation(gui_components=[CHAT_GUI]))
    assert declared["valid"], declared["issues"]
    assert declared["normalized"]["gui_components"] == [{**CHAT_GUI, "version": 1, "config": {}}]
    assert declared["normalized_digest"] != without["normalized_digest"]


@pytest.mark.asyncio
async def test_form_presents_one_of_the_agents_own_workflows_at_its_version(stack) -> None:
    s = stack()
    source = {"kind": "workflow", "key": "flow", "version": "1.2.0"}
    result = await validate(
        s, gui_creation(gui_components=[form_gui(source), {**CHAT_GUI, "default": False}])
    )
    assert result["valid"], result["issues"]
    # The Agent never declares form fields; the Workflow owns them.
    assert result["normalized"]["gui_components"][0]["config"] == {"source": source}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "component,expected",
    [
        (
            form_gui({"kind": "workflow", "key": "audited_flow", "version": "1.2.0"}),
            ("gui_components.0.config.source.key", "gui.workflow_unreferenced"),
        ),
        (
            form_gui({"kind": "workflow", "key": "flow", "version": "9.9.9"}),
            ("gui_components.0.config.source.version", "gui.workflow_version_mismatch"),
        ),
        (
            form_gui(
                {"kind": "contract", "contract": "caos.agent_creation_draft", "schema_version": 1}
            ),
            ("gui_components.0.config.source", "gui.contract_incompatible"),
        ),
    ],
)
async def test_gui_references_must_match_the_agent(stack, component, expected) -> None:
    result = await validate(stack(), gui_creation(gui_components=[component]))
    assert not result["valid"] and expected in codes(result)


@pytest.mark.asyncio
async def test_chat_gui_requires_a_v2_conversational_composition(stack) -> None:
    result = await validate(stack(), gui_creation(type="form", gui_components=[CHAT_GUI]))
    assert ("gui_components.0", "gui.component_incompatible") in codes(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "components",
    [
        [{"type": "tabs", "key": "main", "default": True}],  # unsupported component type
        [{**CHAT_GUI, "children": [CHAT_GUI]}],  # no nesting
        [{**CHAT_GUI, "config": {"model": "gpt"}}],  # strictly validated config
        [form_gui({"kind": "workflow", "key": "flow", "version": "1.2.0"}, fields=[{"key": "x"}])],
        [{**CHAT_GUI, "version": 2}],
        [CHAT_GUI, {**CHAT_GUI, "key": "second"}],  # two defaults
        [{**CHAT_GUI, "default": False}],  # no default
        [CHAT_GUI, {**CHAT_GUI, "default": False}],  # duplicate keys
        [{**CHAT_GUI, "key": "Has Spaces"}],
    ],
)
async def test_gui_structure_is_strict(stack, components) -> None:
    result = await validate(stack(), gui_creation(gui_components=components))
    assert not result["valid"]
    assert all(issue["path"].startswith("gui_components") for issue in result["issues"])
