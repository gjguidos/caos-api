"""Read-only runtime identity for Nataraja (development only, loopback only).

Reports what *this* process actually loaded: boot/reload identity, build metadata, the
UTU policy revision it enforces, Genesis handoff activation modes and the exact synaptic
registrations (signal, provider, version). No secrets, tokens, URLs or definitions; no
state is read from or written to Mongo/Redis. Outside local development, or from a
non-loopback client, the route does not exist (404).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from ipaddress import ip_address
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request

from src.domains.agentverse.security.revocation import policy_version

router = APIRouter(prefix="/internal/dev/nataraja", tags=["Nataraja (dev)"])

# A new process (including every --reload restart) gets a new identity.
BOOT_ID = uuid4().hex
STARTED_AT = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _allowed(request: Request) -> bool:
    if (
        os.environ.get("ENVIRONMENT") != "development"
        or os.environ.get("GENESIS_LOCAL_ENABLED") != "true"
    ):
        return False
    host = request.client.host if request.client else ""
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


@router.get("/runtime")
async def runtime(request: Request) -> dict[str, Any]:
    if not _allowed(request):
        raise HTTPException(404)
    from src.base.services.version_service import VersionService

    av = request.app.container.agentverse
    build = VersionService().get_version()
    registry = av.synaptic_exchange()._registry
    registrations = []
    for signal in sorted(registry.keys()):
        binding = registry.resolve(signal)
        registrations.append(
            {"signal": binding.signal, "provider": binding.provider, "version": binding.version}
        )
    try:
        handoffs = [
            {"slot": h.slot, "agent_id": h.agent_id, "activation": h.activation}
            for h in av.genesis_handoffs()
        ]
        loaded_policy = policy_version(av.synaptic_authorization())
    except Exception:  # noqa: BLE001 - Genesis disabled in this process
        handoffs, loaded_policy = [], None
    return {
        "contract": "caos.nataraja.runtime.v1",
        "process": {"boot_id": BOOT_ID, "started_at": STARTED_AT},
        "build": {"version": build.version, "revision": build.revision, "channel": build.channel},
        "policy_version": loaded_policy,
        "handoffs": sorted(handoffs, key=lambda h: h["slot"]),
        "registrations": registrations,
    }
