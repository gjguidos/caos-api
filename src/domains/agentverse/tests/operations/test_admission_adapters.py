"""OPS2-04C.1 adapters over SynapticRegistry, UTU and CPBX (no new authority)."""

from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.base.events.v2.contracts import EventPermissionError
from src.base.events.v2.policy import EventPrincipal
from src.base.operations.v2.admission import (
    DiscoveryPage,
    DiscoveryRequest,
    DispatchCandidate,
    UnknownCapability,
)
from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import (
    AgentPrincipalResolver,
)
from src.domains.agentverse.ncsc.authorization import SynapticAuthorization
from src.domains.agentverse.ncsc.cpbx import SynapticExchange
from src.domains.agentverse.ncsc.models import Synapse, SynapticGrants
from src.domains.agentverse.operations.admission import (
    SynapticCapabilityCatalog,
    UTUAdmissionAuthority,
)
from src.domains.agentverse.operations.discovery import (
    DISCOVERY_PROVIDER,
    DISCOVERY_SIGNAL,
    DISCOVERY_VERSION,
    register_dispatch_discovery,
)
from src.domains.agentverse.registries.synaptic_registry import SynapticRegistry
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.security.revocation import ExecutionPermit, policy_version
from src.domains.agentverse.security.utu import AuthorizedAgentPrincipal, UTUAuthority


async def forecast(synapse: Synapse, client: Any) -> dict[str, Any]:
    return {}


def registry() -> SynapticRegistry:
    value = SynapticRegistry()
    value.subscribe("weather.forecast.fetch", provider="tool.weather", version="1.2.0")(forecast)
    return value


def test_catalog_resolves_exact_version_and_executable_identity() -> None:
    ref = SynapticCapabilityCatalog(registry()).resolve("weather.forecast.fetch", "1.2.0")
    assert (ref.signal, ref.version, ref.provider) == (
        "weather.forecast.fetch",
        "1.2.0",
        "tool.weather",
    )
    assert ref.executable.endswith("test_admission_adapters:forecast")
    assert not ref.executable.startswith("src.")


@pytest.mark.parametrize(
    "signal,version",
    [
        ("weather.forecast.fetch", "1.0.0"),
        ("weather.forecast.fetch", ""),
        ("weather.unknown", "1.2.0"),
        ("Not-A-Signal", "1.2.0"),
    ],
)
def test_catalog_rejects_unregistered_or_inexact_versions(signal: str, version: str) -> None:
    with pytest.raises(UnknownCapability):
        SynapticCapabilityCatalog(registry()).resolve(signal, version)


KEY = AgentInstanceKey("tenant", "owner", "agent", "secret-session-id")


def utu(*, grants: set[str], versions: tuple[str, ...] | None = None) -> tuple[UTUAuthority, Any]:
    policy = SynapticAuthorization(grants={("tenant", "agent"): SynapticGrants(frozenset(grants))})
    current = (policy_version(policy), "incarnation", "0", "0")
    sessions = SimpleNamespace(get_session=AsyncMock(), bind_revocation_handler=Mock())
    resolver = AgentPrincipalResolver(
        definitions=SimpleNamespace(find_by_id=AsyncMock()),
        sessions=sessions,
        authorization=policy,
        approvals={},
    )
    revocations = SimpleNamespace(read=AsyncMock(return_value=versions or current))
    return UTUAuthority(resolver=resolver, authorization=policy, revocations=revocations), current


def principal(versions: tuple[str, ...]) -> AuthorizedAgentPrincipal:
    return AuthorizedAgentPrincipal("agent", "tenant", ExecutionPermit(KEY, versions))


@pytest.mark.asyncio
async def test_utu_provenance_records_evidence_not_a_permit() -> None:
    authority, versions = utu(grants={"weather.forecast.fetch"})
    evidence = await UTUAdmissionAuthority(authority).provenance(
        principal(versions), "weather.forecast.fetch"
    )
    assert (evidence.tenant_id, evidence.principal_id, evidence.subject_id) == (
        "tenant",
        "agent",
        "owner",
    )
    assert evidence.policy_version == versions[0]
    dumped = evidence.model_dump_json()
    assert "secret-session-id" not in dumped and "incarnation" not in dumped


@pytest.mark.asyncio
async def test_utu_provenance_requires_invoke_grant_for_the_capability() -> None:
    authority, versions = utu(grants={"chat.respond"})
    with pytest.raises(EventPermissionError):
        await UTUAdmissionAuthority(authority).provenance(
            principal(versions), "weather.forecast.fetch"
        )


@pytest.mark.asyncio
async def test_utu_provenance_rejects_superseded_or_mismatched_permits() -> None:
    authority, versions = utu(grants={"weather.forecast.fetch"})
    stale = (versions[0], "old-incarnation", "0", "0")
    with pytest.raises(EventPermissionError):
        await UTUAdmissionAuthority(authority).provenance(
            principal(stale), "weather.forecast.fetch"
        )
    forged = replace(principal(versions), agent_id="other")
    with pytest.raises(EventPermissionError):
        await UTUAdmissionAuthority(authority).provenance(forged, "weather.forecast.fetch")


class Repository:
    def __init__(self) -> None:
        self.calls: list[tuple[DiscoveryRequest, str | None]] = []

    async def admit(self, *args: Any) -> Any:  # pragma: no cover
        raise AssertionError

    async def discover(
        self, request: DiscoveryRequest, *, tenant_id: str | None = None
    ) -> DiscoveryPage:
        self.calls.append((request, tenant_id))
        candidate = DispatchCandidate(
            operation_id=uuid4(),
            tenant_id=tenant_id or "",
            signal="weather.forecast.fetch",
            capability_version="1.2.0",
            correlation_id="corr",
            causation_id=None,
            expected_revision=0,
            expected_generation=0,
            discovery_key=f"{1:020d}:{uuid4()}",
        )
        return DiscoveryPage(candidates=(candidate,), next_after=candidate.discovery_key)


def exchange(grants: dict[tuple[str, str], SynapticGrants]) -> tuple[SynapticExchange, Repository]:
    value, repository = SynapticRegistry(), Repository()
    binding = register_dispatch_discovery(value, repository)
    assert (binding.signal, binding.provider, binding.version) == (
        DISCOVERY_SIGNAL,
        DISCOVERY_PROVIDER,
        DISCOVERY_VERSION,
    )
    assert value.resolve(DISCOVERY_SIGNAL, DISCOVERY_VERSION) is binding
    return SynapticExchange(
        registry=value, authorization=SynapticAuthorization(grants=grants)
    ), repository


@pytest.mark.asyncio
async def test_discovery_capability_is_cpbx_governed_and_tenant_scoped() -> None:
    cpbx, repository = exchange({("tenant-a", "system.enlil"): SynapticGrants({DISCOVERY_SIGNAL})})
    client = cpbx.bind(EventPrincipal("system.enlil", "tenant-a"))
    result = await client.request(DISCOVERY_SIGNAL, {"limit": 5})
    page = DiscoveryPage.model_validate(result)
    assert page.candidates[0].tenant_id == "tenant-a"
    assert repository.calls == [(DiscoveryRequest(limit=5), "tenant-a")]

    with pytest.raises(ValidationError):
        await client.request(DISCOVERY_SIGNAL, {"tenant_id": "tenant-b"})
    with pytest.raises(ValidationError):
        await client.request(DISCOVERY_SIGNAL, {"limit": 10_000})


@pytest.mark.asyncio
async def test_discovery_registration_grants_no_invocation() -> None:
    cpbx, repository = exchange({("tenant-a", "system.enlil"): SynapticGrants({DISCOVERY_SIGNAL})})
    for tenant, agent in (("tenant-b", "system.enlil"), ("tenant-a", "agent.adam")):
        with pytest.raises(EventPermissionError):
            await cpbx.bind(EventPrincipal(agent, tenant)).request(DISCOVERY_SIGNAL, {})
    assert repository.calls == []


def utu_exchange(
    *, versions: tuple[str, ...] | None = None
) -> tuple[SynapticExchange, Repository, tuple[str, ...]]:
    """CPBX exactly as DI wires it: UTUAuthority is the enforced synaptic policy."""
    authority, current = utu(grants={DISCOVERY_SIGNAL}, versions=versions)
    value, repository = SynapticRegistry(), Repository()
    register_dispatch_discovery(value, repository)
    return SynapticExchange(registry=value, authorization=authority), repository, current


@pytest.mark.asyncio
async def test_discovery_through_utu_requires_verified_permit_and_derives_tenant() -> None:
    cpbx, repository, versions = utu_exchange()
    page = await cpbx.bind(principal(versions)).request(DISCOVERY_SIGNAL, {"limit": 2})
    assert DiscoveryPage.model_validate(page).candidates[0].tenant_id == "tenant"
    assert repository.calls == [(DiscoveryRequest(limit=2), "tenant")]


@pytest.mark.asyncio
async def test_discovery_through_utu_denies_unverified_forged_or_revoked_callers() -> None:
    cpbx, repository, versions = utu_exchange()
    callers = [
        EventPrincipal("agent", "tenant"),  # No permit: registration alone grants nothing.
        AuthorizedAgentPrincipal("agent", "other-tenant", ExecutionPermit(KEY, versions)),
        AuthorizedAgentPrincipal("other-agent", "tenant", ExecutionPermit(KEY, versions)),
    ]
    for caller in callers:
        with pytest.raises(EventPermissionError):
            await cpbx.bind(caller).request(DISCOVERY_SIGNAL, {})
    revoked, revoked_repository, _ = utu_exchange(versions=("other-policy", "x", "0", "0"))
    with pytest.raises(EventPermissionError):
        await revoked.bind(principal(versions)).request(DISCOVERY_SIGNAL, {})
    assert repository.calls == [] and revoked_repository.calls == []


@pytest.mark.asyncio
async def test_discovery_handler_never_issues_unscoped_queries() -> None:
    cpbx, repository = exchange({("tenant-a", "system.enlil"): SynapticGrants({DISCOVERY_SIGNAL})})
    client = cpbx.bind(EventPrincipal("system.enlil", "tenant-a"))
    for payload in ({}, {"limit": 1}, {"after": f"{0:020d}:{uuid4()}"}):
        await client.request(DISCOVERY_SIGNAL, payload)
    assert all(tenant == "tenant-a" for _, tenant in repository.calls)
