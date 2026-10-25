"""Real Redis 6 sessions and UTU; use only a disposable UTU_TEST_REDIS_URL."""

import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from jose import jwt

from src.base.events.v2.contracts import EventPermissionError
from src.base.services.jwt_service import JWTService
from src.domains.agentverse.agents.lifecycle.tests import test_utu_revocation as revocation_tests
from src.domains.agentverse.agents.lifecycle.tests.test_utu import KEY
from src.domains.agentverse.agents.lifecycle.tests.test_utu_revocation import (
    client_for,
    environment,
)
from src.domains.agentverse.interfaces.api.v2.genesis_sessions import router as session_router
from src.domains.agentverse.interfaces.api.v2.genesis_sessions import session_socket
from src.domains.agentverse.security.sessions import RedisAgentSessionAuthority

store = revocation_tests.store

SECRET = "session-test-secret-that-is-at-least-32-characters"


@pytest_asyncio.fixture
async def session_env(store, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "local")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("LOCAL_JWT_SECRET", SECRET)
    now = [time.time()]
    claims = dict(
        tid="tenant",
        sub="owner",
        sid="session",
        exp=int(now[0] + 3600),
        iss="CAOS-local",
        aud="CAOS-api",
    )

    def sign(**changes):
        return jwt.encode({**claims, **changes}, SECRET, algorithm="HS256")

    prefix = "test:{" + uuid4().hex + "}:sessions"
    sessions = RedisAgentSessionAuthority(
        repository=store._repo, jwt_service=JWTService(), prefix=prefix, clock=lambda: now[0]
    )
    env = await environment(store)
    env.verifier._sessions = sessions
    sessions.bind_revocation_handler(env.utu.revoke)
    token = sign()
    await sessions.provision(
        token, tenant_id="tenant", user_id="owner", allowed_agent_ids=frozenset({"agent"})
    )
    try:
        yield SimpleNamespace(**locals())
    finally:
        await sessions.close()
        await env.lifecycle.shutdown()
        await store._repo.delete(prefix + ":records")
        await store._repo.delete(prefix + ":due")


@pytest.mark.asyncio
async def test_uuid_alone_and_copied_session_are_denied(session_env):
    s = session_env
    with pytest.raises(EventPermissionError):
        await s.sessions.get_session("session")
    for token in [s.sign(sub="intruder"), s.sign(tid="other"), s.sign(jti="copied-sid")]:
        with pytest.raises(EventPermissionError):
            async with s.sessions.authenticated(token):
                pass
    async with s.sessions.authenticated(s.token):
        for field in ["tenant_id", "user_id", "session_id", "agent_id"]:
            with pytest.raises(EventPermissionError):
                await s.env.utu.resolve_agent_principal(replace(KEY, **{field: "other"}))
        assert (await s.env.utu.resolve_agent_principal(KEY)).agent_id == "agent"
    with pytest.raises(EventPermissionError):
        await s.sessions.get_session("session")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [dict(sid=None), dict(tid=None), dict(exp=None), dict(exp=True), dict(exp=float("inf"))],
)
async def test_required_signed_claims(session_env, change):
    with pytest.raises(EventPermissionError):
        async with session_env.sessions.authenticated(session_env.sign(**change)):
            pass


@pytest.mark.asyncio
async def test_signature_verification_and_provision_identity(session_env):
    s = session_env
    forged = jwt.encode(s.claims, "different-signing-secret", algorithm="HS256")
    with pytest.raises(EventPermissionError):
        async with s.sessions.authenticated(forged):
            pass
    with pytest.raises(EventPermissionError):
        await s.sessions.provision(
            s.sign(sid="new"),
            tenant_id="other",
            user_id="owner",
            allowed_agent_ids=frozenset({"agent"}),
        )


@pytest.mark.asyncio
async def test_logout_revokes_existing_nested_clients_and_lifecycle(session_env):
    s = session_env
    async with s.sessions.authenticated(s.token):
        client, children, _ = await client_for(s.env)
        await s.env.lifecycle.activate(KEY)
        assert await client.request("chat.respond", {}) == "ok"
        await s.sessions.logout()
        for call in [
            lambda: client.request("chat.respond", {}),
            lambda: children[0].request("llm.generate", {}),
            lambda: s.env.lifecycle.activate(KEY),
        ]:
            with pytest.raises(EventPermissionError):
                await call()
    await s.env.lifecycle.shutdown()
    s.env.runtime.stop.assert_awaited_once()
    async with s.sessions.authenticated(s.token, for_logout=True):
        await s.sessions.logout()  # acknowledged retries are idempotent
    with pytest.raises(EventPermissionError):
        await s.sessions.provision(
            s.token, tenant_id="tenant", user_id="owner", allowed_agent_ids=frozenset({"agent"})
        )


@pytest.mark.asyncio
async def test_expiry_fences_saved_client_without_scheduler(session_env):
    s = session_env
    async with s.sessions.authenticated(s.token):
        client, _, _ = await client_for(s.env)
    s.now[0] = s.claims["exp"] + 1
    with pytest.raises(EventPermissionError):
        await client.request("chat.respond", {})
    assert (await s.sessions._record("session"))["status"] == "revoked"


@pytest.mark.asyncio
async def test_invalidation_intent_survives_utu_outage_and_worker_restart(session_env):
    s = session_env
    async with s.sessions.authenticated(s.token):
        client, _, _ = await client_for(s.env)
    original = s.sessions._handler
    s.sessions._handler = AsyncMock(side_effect=EventPermissionError("Redis unavailable"))
    with pytest.raises(EventPermissionError):
        await s.sessions.invalidate("session")
    assert (await s.sessions._record("session"))["status"] == "pending"
    with pytest.raises(EventPermissionError):
        await client.request("chat.respond", {})
    restarted = RedisAgentSessionAuthority(
        repository=s.store._repo, jwt_service=JWTService(), prefix=s.prefix, clock=lambda: s.now[0]
    )
    restarted.bind_revocation_handler(original)
    await restarted.process_due()
    assert (await restarted._record("session"))["status"] == "revoked"


@pytest.mark.asyncio
async def test_redis_unavailable_denies_lookups_and_existing_clients(session_env):
    s = session_env
    async with s.sessions.authenticated(s.token):
        client, _, _ = await client_for(s.env)
    original = s.sessions._repo
    s.sessions._repo = SimpleNamespace(eval=AsyncMock(side_effect=ConnectionError()))
    try:
        with pytest.raises(EventPermissionError):
            async with s.sessions.authenticated(s.token):
                pass
        with pytest.raises(EventPermissionError):
            await client.request("chat.respond", {})
    finally:
        s.sessions._repo = original


@pytest.mark.asyncio
async def test_durable_expiry_replay_and_membership_fence(session_env):
    s = session_env
    s.now[0] = s.claims["exp"] + 1
    await s.sessions.process_due()
    assert (await s.sessions._record("session"))["status"] == "revoked"
    s.now[0] = time.time()
    await s.sessions.remove_membership("tenant", "owner")
    with pytest.raises(EventPermissionError):
        await s.sessions.provision(
            s.sign(sid="new"),
            tenant_id="tenant",
            user_id="owner",
            allowed_agent_ids=frozenset({"agent"}),
        )


@pytest.mark.asyncio
async def test_http_logout_retry_and_default_deny(session_env):
    s = session_env
    app = FastAPI()
    app.include_router(session_router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        assert (await client.get("/api/v2/agentverse/session")).status_code == 403
        app.state.genesis_sessions = s.sessions
        headers = {"Authorization": "Bearer " + s.token}
        assert (await client.get("/api/v2/agentverse/session", headers=headers)).status_code == 200
        original = s.sessions._handler
        s.sessions._handler = AsyncMock(side_effect=EventPermissionError("unavailable"))
        assert (
            await client.post("/api/v2/agentverse/session/logout", headers=headers)
        ).status_code == 503
        s.sessions._handler = original
        assert (
            await client.post("/api/v2/agentverse/session/logout", headers=headers)
        ).status_code == 204
        assert (
            await client.post("/api/v2/agentverse/session/logout", headers=headers)
        ).status_code == 204
        assert (await client.get("/api/v2/agentverse/session", headers=headers)).status_code == 403


@pytest.mark.asyncio
async def test_websocket_rechecks_expiry_after_receive(session_env):
    s = session_env

    async def receive():
        s.now[0] = s.claims["exp"] + 1
        return {"type": "ping"}

    ws = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(genesis_sessions=s.sessions)),
        headers={"authorization": "Bearer " + s.token},
        scope={},
        accept=AsyncMock(),
        receive_json=receive,
        send_json=AsyncMock(),
        close=AsyncMock(),
    )
    await session_socket(ws)
    ws.close.assert_awaited_once_with(code=1008)
    ws.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_startup_bindings_are_opt_in_lazy_and_default_deny(monkeypatch):
    from unittest.mock import Mock

    from src.domains.agentverse.dependencies.genesis_bootstrap import (
        build_genesis_security,
        configure_genesis_transport,
    )

    monkeypatch.delenv("GENESIS_LOCAL_ENABLED", raising=False)
    monkeypatch.delenv("GENESIS_SECURITY_POLICY_FILE", raising=False)
    monkeypatch.setenv("GENESIS_SESSIONS_ENABLED", "false")
    assert build_genesis_security(object()) == {}
    monkeypatch.setenv("GENESIS_SESSIONS_ENABLED", "true")
    repository = SimpleNamespace(eval=AsyncMock(return_value=None))
    provider = AsyncMock(return_value=repository)
    container = SimpleNamespace(
        redis=SimpleNamespace(redis_repository=provider),
        services=SimpleNamespace(jwt_service=lambda: JWTService()),
    )
    bindings = build_genesis_security(container)["genesis_security"]
    assert bindings.approvals == {}
    assert bindings.authorization._grants == {}
    provider.assert_not_called()
    app = FastAPI()
    handler = AsyncMock(return_value=True)
    shared_utu = Mock(side_effect=lambda: bindings.sessions.bind_revocation_handler(handler))
    app.container = SimpleNamespace(agentverse=SimpleNamespace(utu_authority=shared_utu))
    configure_genesis_transport(app, bindings)
    await app.state.start_genesis_sessions()
    shared_utu.assert_called_once()
    await app.state.close_genesis_sessions()
    assert bindings.sessions._handler is handler
    assert bindings.sessions._worker is None


@pytest.mark.asyncio
async def test_inherited_context_cannot_activate_after_request_scope_ends(session_env):
    import asyncio

    s = session_env
    proceed = asyncio.Event()

    async def child():
        await proceed.wait()
        return await s.sessions.get_session("session")

    async with s.sessions.authenticated(s.token):
        task = asyncio.create_task(child())
    proceed.set()
    with pytest.raises(EventPermissionError):
        await task


@pytest.mark.asyncio
async def test_empty_agent_scope_stays_well_formed_after_invalidation(session_env):
    s = session_env
    token = s.sign(sid="empty-scope")
    await s.sessions.provision(
        token, tenant_id="tenant", user_id="owner", allowed_agent_ids=frozenset()
    )
    async with s.sessions.authenticated(token):
        assert not (await s.sessions.get_session("empty-scope")).allowed_agent_ids
        await s.sessions.logout()
    assert (await s.sessions._record("empty-scope"))["status"] == "revoked"


@pytest.mark.asyncio
async def test_idle_lifecycle_observes_expiry_without_request_context(session_env):
    import asyncio

    s = session_env
    async with s.sessions.authenticated(s.token):
        await s.env.lifecycle.activate(KEY)
    s.now[0] = s.claims["exp"] + 1
    async with asyncio.timeout(1):
        while KEY in s.env.lifecycle._active:
            await asyncio.sleep(0.01)
    s.env.runtime.revoke.assert_called()
    s.env.runtime.stop.assert_awaited_once()
