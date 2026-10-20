from dataclasses import dataclass


@dataclass
class ConnCtx:
    session_id: str
    user_id: str | None
    agent_id: str | None
    request: any
    websocket: any
    socket_emitter: any
    agent_system_name: str | None = None
    tenant_id: str | None = None
