"""CATALOG-0.1A provider guards for ``catalog.project@1.0.0``.

Real permits, CPBX client shape and Resource Catalog; UTU authority is a stub so each
failure mode (revoked, changed, ungranted, bootstrap) can be forced. End to end through
the reviewed local policy: test_catalog_capability_integration.py.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.catalog import CatalogListRequest, CatalogRequester
from src.domains.agentverse.catalog.capability import (
    BINDING,
    MAX_PAYLOAD_BYTES,
    PROVIDER,
    RESULT_CONTRACT,
    SCOPE,
    SIGNAL,
    VERSION,
    CatalogCapability,
    catalog_policy_state,
    register_catalog_capability,
    validate_catalog_binding,
)
from src.domains.agentverse.catalog.tests.test_resource_catalog import (  # noqa: F401
    ME,
    SOURCE_AGENT,
    STRANGER,
    env,
)
from src.domains.agentverse.manuscripts.capability import SCOPES as MANUSCRIPT_SCOPES
from src.domains.agentverse.manuscripts.registry import PUBLISHED_FINGERPRINTS
from src.domains.agentverse.ncsc.cpbx import AuthorizedSynapticClient
from src.domains.agentverse.ncsc.models import Synapse, SynapticExecutionContext
from src.domains.agentverse.registries.synaptic_registry import SynapticRegistry
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.security.revocation import ExecutionPermit
from src.domains.agentverse.tests.tools.test_data_validation_tool import REQUESTER, TENANT

KEY = AgentInstanceKey(TENANT, REQUESTER, SOURCE_AGENT, "session")


def permit(**overrides: Any) -> ExecutionPermit:
    fields = {
        "key": KEY,
        "versions": ("policy:9", "incarnation", "0", "0"),
        "origin": "session",
        "scope": SCOPE,
        "binding_chain": (BINDING,),
    }
    return ExecutionPermit(**{**fields, **overrides})


def call(issued: ExecutionPermit, payload: Any, **synapse: Any):
    client = AuthorizedSynapticClient(
        exchange=None,  # type: ignore[arg-type]
        context=SynapticExecutionContext(issued.key.agent_id, issued.key.tenant_id, permit=issued),
    )
    fields = {
        "signal": SIGNAL,
        "source": issued.key.agent_id,
        "target": PROVIDER,
        "tenant_id": issued.key.tenant_id,
        "payload": payload,
        "provider_version": VERSION,
        "entry_operation": SCOPE.operation,
        **synapse,
    }
    return Synapse(**fields), client


def list_cmd(kind: str = "tool", **request: Any) -> dict[str, Any]:
    return {
        "contract": "caos.catalog.project",
        "schema_version": 1,
        "operation": "list",
        "request": {"kind": kind, **request},
    }


def resolve_cmd(kind: str, value: str, **request: Any) -> dict[str, Any]:
    return {
        "contract": "caos.catalog.project",
        "schema_version": 1,
        "operation": "resolve",
        "request": {"kind": kind, "value": value, **request},
    }


@pytest.fixture
def cap(env):  # noqa: F811
    issued = permit()
    authority = SimpleNamespace(
        check_permit=AsyncMock(),
        resolve_agent_principal=AsyncMock(
            return_value=SimpleNamespace(permit=SimpleNamespace(versions=issued.versions))
        ),
        grants_for=Mock(return_value=SimpleNamespace(invoke=frozenset({SIGNAL}))),
        resource_requester=AsyncMock(return_value=ME),
    )
    capability = CatalogCapability(authority=authority, catalog=env.catalog)  # type: ignore[arg-type]

    async def run(payload: Any, issued: ExecutionPermit | None = None, **synapse: Any):
        return await capability.project(*call(issued or permit(), payload, **synapse))

    return SimpleNamespace(env=env, authority=authority, capability=capability, run=run)


# --- Registration and policy ---------------------------------------------------------------


def test_registration_is_exact_and_grants_nothing(cap):
    registry = SynapticRegistry()
    with pytest.raises(ValueError):
        validate_catalog_binding(registry)  # Granted but unbound fails startup.
    register_catalog_capability(registry, cap.capability)
    binding = registry.resolve(SIGNAL, VERSION)
    assert (binding.signal, binding.provider, binding.version) == (SIGNAL, PROVIDER, VERSION)
    validate_catalog_binding(registry)
    assert BINDING.signal == SIGNAL and SCOPE.bindings == frozenset({BINDING})


def test_policy_state_refuses_trusted_lines():
    grants = {("t", "enki"): SimpleNamespace(invoke=frozenset({SIGNAL}))}
    assert catalog_policy_state(SimpleNamespace(_grants=grants, _trusted_lines=()))
    assert not catalog_policy_state(SimpleNamespace(_grants={}, _trusted_lines=()))
    line = SimpleNamespace(target=SIGNAL)
    with pytest.raises(ValueError):
        catalog_policy_state(SimpleNamespace(_grants=grants, _trusted_lines=(line,)))


# --- Caller verification ---------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "issued, synapse",
    [
        (permit(origin="bootstrap"), {}),  # Bootstrap leases never project catalogs.
        # Another capability's (self-consistent) scope.
        (
            permit(
                scope=MANUSCRIPT_SCOPES["status"],
                binding_chain=(MANUSCRIPT_SCOPES["status"].entry,),
            ),
            {},
        ),
        (permit(scope=None, binding_chain=()), {}),  # Unscoped (generic route) permit.
        (permit(binding_chain=()), {}),
        (permit(), {"provider_version": "2.0.0"}),
        (permit(), {"entry_operation": "manuscript.session.status"}),
        (permit(), {"tenant_id": "tenant-b"}),  # Foreign tenant on the synapse.
        (permit(), {"source": "thoth"}),  # Another source Agent.
        (permit(), {"target": "system.manuscript"}),
    ],
)
async def test_only_the_exact_scoped_session_caller_is_served(cap, issued, synapse):
    with pytest.raises(EventPermissionError):
        await cap.run(list_cmd(), issued, **synapse)
    cap.authority.resource_requester.assert_not_awaited()


@pytest.mark.asyncio
async def test_revoked_stale_or_ungranted_authority_is_denied(cap):
    cap.authority.check_permit.side_effect = EventPermissionError("revoked")  # Logout/revocation.
    with pytest.raises(EventPermissionError):
        await cap.run(list_cmd())
    cap.authority.check_permit.side_effect = None
    cap.authority.resolve_agent_principal.return_value = SimpleNamespace(
        permit=SimpleNamespace(versions=("policy:10", "incarnation", "0", "0"))
    )
    with pytest.raises(EventPermissionError):  # Policy changed since the permit was issued.
        await cap.run(list_cmd())
    cap.authority.resolve_agent_principal.return_value = SimpleNamespace(
        permit=SimpleNamespace(versions=permit().versions)
    )
    cap.authority.grants_for.return_value = SimpleNamespace(invoke=frozenset({"chat.context"}))
    with pytest.raises(EventPermissionError):  # Grant withdrawn.
        await cap.run(list_cmd())
    cap.authority.resource_requester.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_result_is_released_after_authority_ends_during_the_read(cap):
    calls = {"n": 0}

    async def check(_):
        calls["n"] += 1
        if calls["n"] > 1:
            raise EventPermissionError("revoked meanwhile")

    cap.authority.check_permit.side_effect = check
    with pytest.raises(EventPermissionError):
        await cap.run(list_cmd())


# --- Strict commands ------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {**list_cmd(), "tenant_id": TENANT},
        {**list_cmd(), "requester": REQUESTER},
        {**list_cmd(), "agent_id": SOURCE_AGENT},
        {**list_cmd(), "provider": "system.catalog"},
        {**list_cmd(), "grants": [SIGNAL]},
        list_cmd(tenant_id="tenant-b"),
        list_cmd(source_agent_id="other"),
        resolve_cmd("tool", "plain", provider="tool.chat"),
        {**list_cmd(), "operation": "write"},
        {**list_cmd(), "operation": "delete"},
        list_cmd("synaptic"),
        list_cmd("tool_runtime_dependency"),
        list_cmd("agent"),  # Registry names are not catalog kinds.
        list_cmd(limit=1000),
        {**list_cmd(), "schema_version": 2},
        {**list_cmd(), "contract": "caos.catalog.list"},
        list_cmd(after="x" * 129),
        resolve_cmd("tool", "x" * (MAX_PAYLOAD_BYTES + 1)),
        "not an object",
        None,
    ],
)
async def test_foreign_fields_unknown_operations_and_kinds_are_rejected(cap, payload):
    result = await cap.run(payload)
    assert result == {
        "contract": RESULT_CONTRACT,
        "ok": False,
        "code": "catalog.invalid_request",
        "retryable": False,
    }


# --- Results: exactly the human's own projection -------------------------------------------


@pytest.mark.asyncio
async def test_results_equal_the_human_requesters_catalog_for_that_source_agent(cap):
    human = CatalogRequester(user=ME, source_agent_id=SOURCE_AGENT)
    for kind in ("agent_type", "role", "soul", "tool", "workflow", "rulepack", "manuscript"):
        served = await cap.run(list_cmd(kind, limit=100))
        expected = await cap.env.catalog.list(human, CatalogListRequest(kind=kind, limit=100))
        assert served["ok"] and served["operation"] == "list"
        assert served["result"] == expected.model_dump(mode="json"), kind
        for entry in served["result"]["entries"]:
            assert entry["decisions"]["execution_authorization"] == "not_evaluated"
            resolved = await cap.run(
                resolve_cmd(kind, entry["ref"]["key"], version=entry["ref"]["version"])
            )
            assert resolved["result"]["entry"] == entry, (kind, entry["ref"]["key"])
    manuscripts = (await cap.run(list_cmd("manuscript")))["result"]["entries"]
    assert [m["ref"]["fingerprint"] for m in manuscripts] == list(PUBLISHED_FINGERPRINTS.values())


@pytest.mark.asyncio
async def test_requester_comes_only_from_the_trusted_session_adapter(cap):
    cap.authority.resource_requester.return_value = STRANGER
    served = await cap.run(list_cmd("tool", limit=100))
    keys = {e["ref"]["key"] for e in served["result"]["entries"]}
    assert "foreign_tool" in keys and "tenant_tool" not in keys
    # The stranger has no gateway binding with this source Agent: no Manuscripts.
    assert (await cap.run(list_cmd("manuscript")))["result"]["entries"] == []


@pytest.mark.asyncio
async def test_hidden_and_unknown_resolutions_are_uniform(cap):
    outcomes = [
        (await cap.run(resolve_cmd("tool", key)))["result"]
        for key in ("core", "others_private", "foreign_tool", "does_not_exist")
    ]
    assert all(o == outcomes[0] for o in outcomes)
    assert outcomes[0]["status"] == "unavailable" and outcomes[0]["entry"] is None


@pytest.mark.asyncio
async def test_projection_never_mutates_registries(cap):
    regs = cap.env.regs
    before = {k: copy.deepcopy([i.model_dump() for i in r.items()]) for k, r in regs.items()}
    await cap.run(list_cmd("role", candidate={"type": "chat", "tools": ["probe"]}))
    await cap.run(resolve_cmd("tool", "probe", candidate={"rule_pack_ids": ["gdpr"]}))
    assert before == {k: [i.model_dump() for i in r.items()] for k, r in regs.items()}
    assert set(cap.env.db.reads) <= {"find_one", "find_all"}
