"""EnkiBootstrap decisions over fakes; the governed path is covered end to end in
test_genesis_bootstrap_integration.py."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.agents.genesis.bootstrap import (
    EnkiBootstrap,
    GenesisBootstrap,
    GenesisBootstrapError,
)
from src.domains.agentverse.dependencies.genesis_local import ENKI_HANDOFF, ENKI_PROVISION

ENTRY = GenesisBootstrap.model_validate(
    json.loads(Path("config/genesis.local.json").read_text())["bootstrap"][0]
)


def coordinator(*responses, find=None, deadline=60.0):
    replies = list(responses)
    leases = []

    @asynccontextmanager
    async def bootstrap_lease(**kwargs):
        leases.append(kwargs)
        yield SimpleNamespace(session_id=f"lease-{len(leases)}")

    async def request(signal, payload):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    client = SimpleNamespace(request=request, revoke=Mock(), drain=AsyncMock())
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)

    boot = EnkiBootstrap(
        entry=ENTRY,
        handoff=ENKI_HANDOFF,
        provision=ENKI_PROVISION,
        definitions=SimpleNamespace(find_by_id=find or AsyncMock(return_value=None)),
        sessions=SimpleNamespace(bootstrap_lease=bootstrap_lease),
        authority=SimpleNamespace(resolve_agent_principal=AsyncMock()),
        exchange=SimpleNamespace(bind=Mock(return_value=client)),
        log=SimpleNamespace(history=AsyncMock(return_value=[])),
        deadline=deadline,
        sleep=sleep,
        clock=lambda: float(sum(sleeps)),
    )
    return boot, leases, sleeps, client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    [
        {"status": "rejected", "stage": "validation", "retryable": False},
        {"status": "failed", "stage": "store", "retryable": False},
    ],
)
async def test_definitive_genesis_outcomes_fail_startup_without_retry(outcome):
    boot, leases, sleeps, client = coordinator(outcome)
    with pytest.raises(GenesisBootstrapError, match="definitively"):
        await boot.ensure()
    assert (len(leases), sleeps) == (1, [])
    client.revoke.assert_called_once()  # the CPBX client never outlives its lease


@pytest.mark.asyncio
async def test_retryable_outcomes_are_bounded_by_the_deadline():
    retry = [
        {"status": "in_progress", "retryable": True},
        {"status": "audit_unavailable", "retryable": True},
        EventPermissionError("Genesis session authority unavailable"),
    ] * 10
    boot, leases, sleeps, _ = coordinator(*retry, deadline=20.0)
    with pytest.raises(GenesisBootstrapError, match="deadline"):
        await boot.ensure()
    assert sleeps == [1.0, 2.0, 4.0, 8.0]  # exponential backoff inside the deadline
    assert len(leases) == len(sleeps) + 1  # one fresh, revoked lease per attempt


@pytest.mark.asyncio
async def test_lease_is_scoped_to_the_policy_entry():
    boot, leases, _, _ = coordinator(EventPermissionError("denied"), deadline=0.5)
    with pytest.raises(GenesisBootstrapError):
        await boot.ensure()
    assert leases == [
        {
            "tenant_id": ENTRY.tenant_id,
            "user_id": ENTRY.subject_id,
            "allowed_agent_ids": frozenset(ENTRY.agents),
            "seconds": boot._lease_seconds,
        }
    ]


@pytest.mark.asyncio
async def test_unconfirmed_provisioning_fails_startup():
    # Genesis claims success, but neither the definition nor its evidence exists.
    boot, *_ = coordinator(
        {"status": "provisioned", "agent_id": ENKI_HANDOFF.agent_id, "correlation_id": "c"}
    )
    with pytest.raises(GenesisBootstrapError, match="definition"):
        await boot.ensure()
