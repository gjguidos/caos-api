"""Exercise real local composition. Creates then permanently revokes a fresh dev session.

Run inside API: python -m scripts.verify_genesis_local (or pass this file via stdin).
No Redis reset or policy replacement. Never prints JWTs or secrets.
"""

import asyncio
import os
import time
from uuid import uuid4

import httpx
from jose import jwt

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.dependencies.genesis_local import ADAM, OWNER, TENANT, require_local
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey


async def main() -> None:
    require_local()
    from src.main import app

    async with app.router.lifespan_context(app):
        av = app.container.agentverse
        sessions = av.genesis_sessions()
        utu = av.utu_authority()
        sid = uuid4().hex
        token = jwt.encode(
            {
                "tid": TENANT,
                "sub": OWNER,
                "oid": OWNER,
                "sid": sid,
                "exp": int(time.time()) + 300,
                "iss": "CAOS-local",
                "aud": "CAOS-api",
            },
            os.environ["LOCAL_JWT_SECRET"],
            algorithm="HS256",
        )
        key = AgentInstanceKey(TENANT, OWNER, ADAM, sid)
        headers = {"Authorization": "Bearer " + token}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://local"
        ) as http:
            assert (
                await http.post("/api/v2/agentverse/session/local", headers=headers)
            ).status_code == 201
            try:
                async with sessions.authenticated(token):
                    principal = await utu.resolve_agent_principal(key)
                    await av.khepri().awaken(key)
                    runtime = av.instance_registry_v2().get(key)
                    client = runtime.ncsc
                    result = await client.request("chat.context", {"text": "Adam verification"})
                    assert result["usage_meta"]["provider"] == "chat_v2"
                response = await http.post("/api/v2/agentverse/session/logout", headers=headers)
                assert response.status_code == 204
                for operation in [
                    lambda: client.request("chat.context", {"text": "old client"}),
                    lambda: utu.check_permit(principal.permit),
                ]:
                    try:
                        await operation()
                    except EventPermissionError:
                        pass
                    else:
                        raise AssertionError("Revoked authority accepted")
                assert (
                    await http.get("/api/v2/agentverse/session", headers=headers)
                ).status_code == 403
                assert (
                    await http.post("/api/v2/agentverse/session/local", headers=headers)
                ).status_code == 403
                av.anubis().revoke_local(TENANT, session_id=sid)
                async with asyncio.timeout(5):
                    while av.instance_registry_v2().exists(key):
                        await asyncio.sleep(0.01)
                    while av.agent_lifecycle_service()._cleanup_tasks:
                        await asyncio.sleep(0.01)
                assert not runtime.is_active
                assert await av.agent_presence_store().get_owner(key) is None
                print(
                    "GENESIS_LOCAL_PASS: JWT -> trusted provisioning -> UTU -> Khepri -> chat.context -> logout -> retained client/permit denied -> Anubis registry and lease cleanup"
                )
            finally:
                await sessions.invalidate(sid)


if __name__ == "__main__":
    asyncio.run(main())
