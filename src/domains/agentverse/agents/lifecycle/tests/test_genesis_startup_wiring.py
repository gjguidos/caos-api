"""Production-style Genesis startup: the real Container, initialize_domains namespace and
genesis bootstrap, with only the Redis session worker loop stubbed (no network I/O)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

from src.domains.agentverse.agents.genesis.initiation import SIGNAL as INITIATION_SIGNAL
from src.domains.agentverse.registries.synaptic_registry import SynapticRegistry
from src.domains.agentverse.security.sessions import RedisAgentSessionAuthority
from src.domains.agentverse.tools.store_tool.spec import (
    STORE_CREATE_PROVISIONED_SIGNAL,
    STORE_CREATE_SIGNAL,
)
from src.domains.agentverse.tools.validation_tool.spec import DATA_VALIDATION_SIGNAL

LOCAL_POLICY = Path("config/genesis.local.json")


async def start_production_app(
    monkeypatch: pytest.MonkeyPatch, policy_file: Path
) -> tuple[FastAPI, SynapticRegistry]:
    from src.base.dependencies.di_container import Container
    from src.domains import initialize_domains
    from src.domains.agentverse import registries
    from src.domains.agentverse.dependencies.genesis_bootstrap import (
        build_genesis_security,
        configure_genesis_transport,
    )

    monkeypatch.setenv("GENESIS_SESSIONS_ENABLED", "true")
    monkeypatch.setenv("GENESIS_SECURITY_POLICY_FILE", str(policy_file))
    monkeypatch.delenv("GENESIS_LOCAL_ENABLED", raising=False)  # no seed writes
    registry = SynapticRegistry()
    monkeypatch.setattr(registries, "synaptic_registry_instance", registry)

    async def start(self: RedisAgentSessionAuthority) -> None:
        # The real start requires UTU's revocation handler; keep that check, skip the
        # Redis subscription loop.
        assert self._handler is not None, "UTU must bind session revocation first"

    async def close(self: RedisAgentSessionAuthority) -> None:
        return None

    monkeypatch.setattr(RedisAgentSessionAuthority, "start", start)
    monkeypatch.setattr(RedisAgentSessionAuthority, "close", close)

    # Same sequence as initialize_app(app, domain_security_factory=build_genesis_security).
    container = Container()
    security = build_genesis_security(container)
    container = initialize_domains(container, **security)
    app = FastAPI()
    app.container = container  # type: ignore[attr-defined]
    configure_genesis_transport(app, security["genesis_security"])
    await app.state.start_genesis_sessions()
    await app.state.close_genesis_sessions()
    return app, registry


@pytest.mark.asyncio
async def test_local_policy_startup_registers_genesis_capabilities_from_the_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, registry = await start_production_app(monkeypatch, LOCAL_POLICY)
    av = app.container.agentverse  # type: ignore[attr-defined]
    assert DATA_VALIDATION_SIGNAL in registry
    assert STORE_CREATE_PROVISIONED_SIGNAL in registry
    assert "chat.response" in registry
    assert STORE_CREATE_SIGNAL not in registry  # never granted by the local policy
    assert INITIATION_SIGNAL not in registry  # this fixture disables local bootstrap
    # The registered handlers are the DI singletons, not independently built tools.
    provisioned = registry.resolve(STORE_CREATE_PROVISIONED_SIGNAL, "1.0.0").handler
    assert provisioned.__self__._tool is av.store().tool()
    assert dict(provisioned.__self__._provisions) == dict(av.genesis_provisions())
    validation = registry.resolve(DATA_VALIDATION_SIGNAL, "1.0.0").handler
    assert validation.__self__ is av.validation().tool()


@pytest.mark.asyncio
@pytest.mark.parametrize("drop", ["provisioned", "grant"])
async def test_provisioned_store_requires_both_the_grant_and_a_pinned_slot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, drop: str
) -> None:
    config: dict[str, Any] = json.loads(LOCAL_POLICY.read_text())
    if drop == "provisioned":
        config["provisioned"] = []
        config["bootstrap"] = []  # a bootstrap lease requires its pinned identity
    else:
        for grant in config["grants"]:
            grant["invoke"] = [s for s in grant["invoke"] if s != STORE_CREATE_PROVISIONED_SIGNAL]
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps(config))
    _, registry = await start_production_app(monkeypatch, policy)
    assert STORE_CREATE_PROVISIONED_SIGNAL not in registry
    assert STORE_CREATE_SIGNAL not in registry
    assert DATA_VALIDATION_SIGNAL in registry  # still granted to Genesis


def test_namespace_exposes_the_providers_genesis_startup_resolves() -> None:
    from src.base.dependencies.di_container import Container
    from src.domains import initialize_domains

    av = initialize_domains(Container()).agentverse
    for name in (
        "store",
        "validation",
        "genesis_provisions",
        "genesis_citizens",
        "utu_authority",
        "db_service",
    ):
        assert callable(getattr(av, name)), name


@pytest.mark.asyncio
async def test_local_policy_declares_joshua_and_genesis_as_utu_system_citizens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.domains.agentverse.dependencies import genesis_local

    app, _ = await start_production_app(monkeypatch, LOCAL_POLICY)
    av = app.container.agentverse  # type: ignore[attr-defined]
    citizens = dict(av.genesis_citizens())
    assert citizens == genesis_local.CITIZENS
    assert (genesis_local.TENANT, genesis_local.ENKI) not in citizens
    # The legacy Adam bootstrap identity is gone from the reviewed policy.
    assert not any(agent.endswith("000000000002") for _, agent in av.genesis_approvals())
    # The one UTU verifier receives the same trusted snapshot; no parallel authority.
    assert av.utu_authority()._resolver._citizens == citizens
    # Citizenship changes no grant and no policy digest.
    grants = av.synaptic_authorization()._grants
    assert grants[(genesis_local.TENANT, genesis_local.JOSHUA)].invoke == {INITIATION_SIGNAL}
    assert grants[(genesis_local.TENANT, genesis_local.GENESIS)].invoke == {
        DATA_VALIDATION_SIGNAL,
        STORE_CREATE_PROVISIONED_SIGNAL,
    }


@pytest.mark.parametrize(
    "change,error",
    [
        (lambda c: c["system_citizens"].append(dict(c["system_citizens"][0])), "Duplicate"),
        (lambda c: c["system_citizens"][0].update(extra=True), "exactly"),
        (lambda c: c["system_citizens"][0].pop("name"), "exactly"),
        (lambda c: c.update(approvals=c["approvals"][:1]), "approval"),
        (
            lambda c: c["system_citizens"].append(
                {
                    "tenant_id": "caos-local",
                    "agent_id": "00000000-0000-4000-8000-000000000004",
                    "name": "Enki",
                }
            ),
            "Cognitive Agent",
        ),
        (lambda c: c["system_citizens"][0].update(name=" "), "non-empty"),
    ],
)
def test_system_citizen_policy_is_strict(change: Any, error: str) -> None:
    from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import (
        AgentActivationApproval,
        AgentPrincipalResolver,
    )
    from src.domains.agentverse.dependencies.genesis_bootstrap import (
        _citizens,
        _handoffs,
        _provisions,
    )

    config: dict[str, Any] = json.loads(LOCAL_POLICY.read_text())
    change(config)
    approvals = {
        (a["tenant_id"], a["agent_id"]): AgentActivationApproval(**a) for a in config["approvals"]
    }
    provisions = _provisions(config["provisioned"], approvals)
    with pytest.raises((ValueError, TypeError), match=error):
        citizens = _citizens(
            config["system_citizens"], approvals, provisions, _handoffs(config["handoffs"])
        )
        # Loader accepted it: the UTU verifier must still reject the record.
        AgentPrincipalResolver(
            definitions=None,  # type: ignore[arg-type]
            sessions=None,  # type: ignore[arg-type]
            authorization=None,  # type: ignore[arg-type]
            citizens=citizens,
        )


ENKI_ID = "00000000-0000-4000-8000-000000000004"


def _load_bootstrap(config: dict[str, Any]) -> Any:
    from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import (
        AgentActivationApproval,
    )
    from src.domains.agentverse.dependencies.genesis_bootstrap import (
        _bootstrap,
        _citizens,
        _handoffs,
        _provisions,
    )

    approvals = {
        (a["tenant_id"], a["agent_id"]): AgentActivationApproval(**a) for a in config["approvals"]
    }
    provisions = _provisions(config["provisioned"], approvals)
    handoffs = _handoffs(config["handoffs"])
    citizens = _citizens(config["system_citizens"], approvals, provisions, handoffs)
    return _bootstrap(config["bootstrap"], provisions, handoffs, citizens)


def test_local_policy_declares_one_bootstrap_lease_for_the_two_citizens() -> None:
    (entry,) = _load_bootstrap(json.loads(LOCAL_POLICY.read_text()))
    assert set(entry.agents) == {
        "00000000-0000-4000-8000-000000000005",
        "00000000-0000-4000-8000-000000000003",
    }
    assert (entry.creation["type"], entry.creation["role_name"]) == ("form", "enki")
    assert entry.creation["personality_key"] == "enki"


@pytest.mark.parametrize(
    "change,error",
    [
        (lambda b: b.append(dict(b[0])), "Duplicate"),
        (lambda b: b[0].update(extra=True), "Extra inputs"),
        (lambda b: b[0].update(subject_id="someone-else"), "handoff"),
        (lambda b: b[0].update(slot="other"), "slot"),
        (lambda b: b[0]["agents"].append(ENKI_ID), "initiator and executor"),
        (lambda b: b[0].update(agents=b[0]["agents"][:1]), "initiator and executor"),
        (lambda b: b[0]["creation"].update(type="chat"), "pinned identity"),
        (lambda b: b[0]["creation"].update(name="Adam"), "pinned identity"),
        (lambda b: b[0]["creation"].update(role_name="general"), "composition"),
        (lambda b: b[0]["creation"].update(personality_key="other"), "composition"),
        # The pre-Soul personality is no longer a reviewed Enki composition.
        (lambda b: b[0]["creation"].update(personality_key="empty_eva"), "composition"),
        # The migration allowance is gone; an unknown policy field is rejected.
        (lambda b: b[0].update(compatible_personality_keys=["empty_eva"]), "Extra inputs"),
    ],
)
def test_bootstrap_policy_is_strict(change: Any, error: str) -> None:
    config: dict[str, Any] = json.loads(LOCAL_POLICY.read_text())
    change(config["bootstrap"])
    with pytest.raises((ValueError, TypeError), match=error):
        _load_bootstrap(config)


def test_bootstrap_requires_declared_citizens() -> None:
    config: dict[str, Any] = json.loads(LOCAL_POLICY.read_text())
    config["system_citizens"] = config["system_citizens"][:1]
    with pytest.raises(ValueError):
        _load_bootstrap(config)
