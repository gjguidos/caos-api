"""Authenticated Genesis session controls; V1 routes retain their own semantics."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from starlette.requests import HTTPConnection

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import VerifiedAgentSession
from src.domains.agentverse.security.sessions import RedisAgentSessionAuthority

router = APIRouter(prefix="/api/v2/agentverse/session", tags=["Genesis Sessions"])


def _authority(connection: HTTPConnection) -> RedisAgentSessionAuthority:
    sessions = getattr(connection.app.state, "genesis_sessions", None)
    if sessions is None:
        raise EventPermissionError("Genesis sessions are not configured")
    return sessions


def _token(connection: HTTPConnection) -> str:
    value = connection.headers.get("authorization", "")
    if not value.startswith("Bearer "):
        raise EventPermissionError("Genesis requires a bearer token")
    return value[7:]


@asynccontextmanager
async def authenticated_genesis(
    connection: HTTPConnection,
    *,
    for_logout: bool = False,
) -> AsyncIterator[VerifiedAgentSession]:
    """Use around the entire trusted HTTP/WS lifecycle call, never a UUID lookup."""
    sessions = _authority(connection)
    async with sessions.authenticated(_token(connection), for_logout=for_logout) as session:
        connection.scope["genesis_session"] = session
        try:
            yield session
        finally:
            connection.scope.pop("genesis_session", None)


@router.get("")
async def session_status(request: Request) -> dict[str, str]:
    try:
        async with authenticated_genesis(request) as session:
            return {"session_id": session.session_id, "tenant_id": session.tenant_id}
    except EventPermissionError as exc:
        raise HTTPException(403, "Genesis session unavailable or denied") from exc


@router.post("/logout", status_code=204)
async def logout(request: Request) -> None:
    try:
        async with authenticated_genesis(request, for_logout=True):
            await _authority(request).logout()
    except EventPermissionError as exc:
        # Never acknowledge invalidation when Redis/UTU completion is uncertain.
        raise HTTPException(503, "Genesis logout not acknowledged; retry") from exc


@router.websocket("/ws")
async def session_socket(websocket: WebSocket) -> None:
    """Bearer-header authenticated channel with continuing expiry/revocation checks.

    Browser clients need a server-side proxy that forwards the bearer header.
    No token is accepted in a query string on this V2 endpoint.
    """
    try:
        async with authenticated_genesis(websocket) as session:
            await websocket.accept()
            while True:
                await _authority(websocket).get_session(session.session_id)
                try:
                    message = await asyncio.wait_for(websocket.receive_json(), timeout=0.5)
                except TimeoutError:
                    continue
                await _authority(websocket).get_session(session.session_id)
                if message == {"type": "ping"}:
                    await websocket.send_json({"type": "pong"})
    except EventPermissionError:
        await websocket.close(code=1008)
    except WebSocketDisconnect:
        pass


@router.post("/local", status_code=201)
async def provision_local(request: Request) -> dict[str, str]:
    """Local issuer bridge. All membership/grants come from server policy."""
    from src.domains.agentverse.dependencies.genesis_local import (
        JOSHUA,
        OWNER,
        TENANT,
        require_local,
    )

    try:
        require_local()
        av = request.app.container.agentverse
        token = _token(request)
        claims = await request.app.container.services.jwt_service().verify_token(token)
        if not claims or (
            claims.get("tid"),
            claims.get("sub"),
            claims.get("oid", claims.get("sub")),
        ) != (TENANT, OWNER, OWNER):
            raise EventPermissionError("Local identity denied")
        # Membership is exactly the enabled server approvals owned by the local operator.
        allowed = frozenset(
            agent
            for (tenant, agent), approval in av.genesis_approvals().items()
            if tenant == TENANT and approval.enabled and approval.owner_user_id == OWNER
        )
        if JOSHUA not in allowed:
            raise EventPermissionError("Local membership denied")
        definitions = av.db_service()
        citizens = av.genesis_citizens()
        for agent in sorted(allowed):
            if (TENANT, agent) in citizens:
                # Deterministic system citizen: trusted policy identity, no definition.
                continue
            definition = await definitions.find_by_id(agent)
            if definition is None:
                # A pinned identity (Enki) may not exist yet; activation still
                # requires its persisted, matching definition.
                continue
            if definition.get("creator") != OWNER or definition.get("tenant_id") != TENANT:
                raise EventPermissionError("Local ownership denied")
        sid = await _authority(request).provision(
            token, tenant_id=TENANT, user_id=OWNER, allowed_agent_ids=allowed
        )
        return {"session_id": sid, "tenant_id": TENANT}
    except EventPermissionError as exc:
        raise HTTPException(403, "Local session provisioning denied") from exc
