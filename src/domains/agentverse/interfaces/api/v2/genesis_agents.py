"""Session-bound V2 lifecycle adapters; no caller-written identity or grants."""

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.interfaces.api.v2.genesis_sessions import authenticated_genesis
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey

router = APIRouter(prefix="/api/v2/agentverse/agents", tags=["Genesis Agents"])


async def _lifecycle(request: Request, agent_id: str, operation: str) -> dict[str, Any]:
    try:
        async with authenticated_genesis(request) as session:
            key = AgentInstanceKey(session.tenant_id, session.user_id, agent_id, session.session_id)
            av = request.app.container.agentverse
            if operation == "awaken":
                result = await av.khepri().awaken(key)
            elif operation == "sleep":
                result = await av.anubis().sleep(key)
            else:
                result = await av.khepri().status(key)
            return {
                "agent_id": agent_id,
                "session_id": session.session_id,
                "state": result.state.value,
            }
    except EventPermissionError as exc:
        raise HTTPException(403, "Genesis operation denied") from exc


@router.post("/{agent_id}/{operation}")
async def lifecycle(
    request: Request, agent_id: str, operation: Literal["awaken", "sleep"]
) -> dict[str, Any]:
    return await _lifecycle(request, agent_id, operation)


@router.get("/{agent_id}")
async def status(request: Request, agent_id: str) -> dict[str, Any]:
    return await _lifecycle(request, agent_id, "status")


@router.post("/{agent_id}/signals/{signal}")
async def signal(request: Request, agent_id: str, signal: str, payload: dict[str, Any]) -> Any:
    try:
        async with authenticated_genesis(request) as session:
            key = AgentInstanceKey(session.tenant_id, session.user_id, agent_id, session.session_id)
            av = request.app.container.agentverse
            await av.utu_authority().authorize_lifecycle(key, operation="activate")
            runtime = av.instance_registry_v2().get(key)
            if runtime is None or not runtime.is_active:
                raise HTTPException(409, "Awaken this session's agent first")
            return await runtime.ncsc.request(signal, payload)
    except EventPermissionError as exc:
        raise HTTPException(403, "Genesis signal denied") from exc
