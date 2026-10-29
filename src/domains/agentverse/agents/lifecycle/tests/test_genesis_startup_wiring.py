"""Production-style Genesis startup: the real Container, initialize_domains namespace and
genesis bootstrap, with only the Redis session worker loop stubbed (no network I/O)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

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
    for name in ("store", "validation", "genesis_provisions", "utu_authority", "db_service"):
        assert callable(getattr(av, name)), name
