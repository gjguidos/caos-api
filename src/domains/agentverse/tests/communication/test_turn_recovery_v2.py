"""CONV-REC-0.1: owned, renewable, fenced turn leases and lazy crash recovery.

Real (disposable) Redis, UTU, lifecycle, CPBX and the V2 socket route; short leases.
"""

import asyncio
import json
import time

import pytest

from src.domains.agentverse.agents.lifecycle.khepri import Khepri
from src.domains.agentverse.entities.history_v2 import ConversationMessageV2
from src.domains.agentverse.interfaces.api.v2.communication import communication_socket
from src.domains.agentverse.repositories.conversation_v2 import (
    ConversationRepositoryV2,
    TurnLease,
    TurnOwnershipLost,
)
from src.domains.agentverse.services.client_event_dispatcher_v2 import ClientEventDispatcherV2
from src.domains.agentverse.tests.communication.test_communication_v2 import (
    Socket,
    communication,  # noqa: F401 - fixture re-export
    issue,
    session_env,  # noqa: F401 - fixture re-export
    store,  # noqa: F401 - fixture re-export
)

LEASE_MS = 300


def short(c, **bounds) -> ConversationRepositoryV2:
    return ConversationRepositoryV2(
        c.s.store._repo,
        prefix=c.prefix,
        **({"lease_ms": LEASE_MS, "max_turn_ms": 1_000, "grace_ms": 200} | bounds),
    )


def event(c, name: str, **changes):
    return c.event.model_copy(update={"event_id": name, **changes})


async def expire(c, repository: ConversationRepositoryV2) -> None:
    """Simulate a crashed owner: its lease simply stops being renewed and runs out."""
    turn = repository._keys("tenant", c.conversation.id)[2]
    await c.s.store._repo.redis.pexpire(turn, 1)
    await asyncio.sleep(0.01)


def message(c, name: str) -> ConversationMessageV2:
    return ConversationMessageV2(
        id=f"{name}-user",
        tenant_id="tenant",
        conversation_id=c.conversation.id,
        event_id=name,
        turn_user_id="owner",
        participant_id="owner",
        kind="user_message",
        content="hello",
    )


def using(c, repository: ConversationRepositoryV2) -> ClientEventDispatcherV2:
    dispatcher = ClientEventDispatcherV2(
        conversations=repository,
        authority=c.e.utu,
        khepri=Khepri(lifecycle=c.e.lifecycle),
        instances=c.e.instances,
        composition=c.dispatcher.composition,
    )
    c.app.container.agentverse.conversation_repository_v2 = lambda: repository
    c.app.container.agentverse.client_event_dispatcher_v2 = lambda: dispatcher
    return dispatcher


@pytest.mark.asyncio
async def test_normal_completion_commits_one_immutable_result(communication):  # noqa: F811
    c = communication
    repository = short(c)
    status, lease = await repository.begin("tenant", "owner", c.event)
    assert status == "started" and isinstance(lease, TurnLease) and lease.fence >= 1
    response = {"type": "chat.context", "results": []}
    await repository.finish(
        "tenant",
        "owner",
        c.event,
        response,
        lease=lease,
        messages=(message(c, c.event.event_id),),
        expected_document=c.conversation,
    )
    assert await repository.begin("tenant", "owner", c.event) == ("replay", response)
    with pytest.raises(TurnOwnershipLost):  # A second commit of the same turn is refused.
        await repository.finish("tenant", "owner", c.event, {"type": "other"}, lease=lease)
    assert await repository.begin("tenant", "owner", c.event) == ("replay", response)
    assert len(await repository.read_messages("tenant", c.conversation.id)) == 1
    # The lease is released at commit: the next event is admitted with a higher fence.
    status, following = await repository.begin("tenant", "owner", event(c, "next"))
    assert status == "started" and following.fence > lease.fence


@pytest.mark.asyncio
async def test_lease_is_owned_renewable_and_bounded_by_its_ceiling(communication):  # noqa: F811
    c = communication
    repository = short(c, max_turn_ms=600, grace_ms=200)
    _, lease = await repository.begin("tenant", "owner", c.event)
    turn = repository._keys("tenant", c.conversation.id)[2]
    assert 0 < await c.s.store._repo.redis.pttl(turn) <= LEASE_MS
    for forged in (
        TurnLease(lease.tenant_id, lease.conversation_id, lease.identity, lease.fence, "other"),
        TurnLease(
            lease.tenant_id, lease.conversation_id, lease.identity, lease.fence + 1, lease.owner
        ),
        TurnLease(
            lease.tenant_id, lease.conversation_id, '["owner","x"]', lease.fence, lease.owner
        ),
    ):
        assert not await repository.renew(forged)
    started = time.monotonic()
    while await repository.renew(lease):
        await asyncio.sleep(0.05)
    # Renewal stops at the hard ceiling (max turn + grace), never indefinitely.
    assert 0.6 <= time.monotonic() - started + 0.05 <= 1.2
    await asyncio.sleep(LEASE_MS / 1000 + 0.05)
    assert await c.s.store._repo.redis.exists(turn) == 0
    with pytest.raises(ValueError):
        ConversationRepositoryV2(c.s.store._repo, lease_ms=0)


@pytest.mark.asyncio
async def test_concurrent_admission_and_duplicate_event_ids(communication):  # noqa: F811
    c = communication
    repository = short(c, lease_ms=5_000, max_turn_ms=10_000)
    outcomes = await asyncio.gather(
        *(repository.begin("tenant", "owner", event(c, f"e-{i}")) for i in range(10))
    )
    assert sorted(status for status, _ in outcomes) == ["busy"] * 9 + ["started"]
    (winner,) = [(i, lease) for i, (s, lease) in enumerate(outcomes) if s == "started"]
    duplicates = await asyncio.gather(
        *(repository.begin("tenant", "owner", event(c, f"e-{winner[0]}")) for _ in range(5))
    )
    assert [status for status, _ in duplicates] == ["pending"] * 5
    changed = event(c, f"e-{winner[0]}", message="different")
    assert (await repository.begin("tenant", "owner", changed))[0] == "conflict"


@pytest.mark.asyncio
async def test_crashed_owner_is_recovered_on_next_admission(communication):  # noqa: F811
    c = communication
    repository = short(c)
    crashed = event(c, "crashed")
    status, lease = await repository.begin("tenant", "owner", crashed)
    assert status == "started"
    # While the lease is live, the conversation is busy and the event is pending.
    assert (await repository.begin("tenant", "owner", event(c, "later")))[0] == "busy"
    assert (await repository.begin("tenant", "owner", crashed))[0] == "pending"
    await asyncio.sleep(LEASE_MS / 1000 + 0.1)  # The owner crashed: nobody renews.
    # A later authorized admission recovers the conversation (no background worker).
    status, following = await repository.begin("tenant", "owner", event(c, "later"))
    assert status == "started" and following.fence == lease.fence + 1
    status, recorded = await repository.begin("tenant", "owner", crashed)
    assert status == "replay"
    assert recorded["type"] == "event.error" and recorded["code"] == "interrupted"
    # Expiry is not proof that side effects failed.
    assert recorded["outcome"] == "unknown"
    assert recorded["correlation_id"] == "crashed"
    assert recorded["conversation_id"] == c.conversation.id
    # Immutable: every replay returns the identical recorded result.
    assert await repository.begin("tenant", "owner", crashed) == ("replay", recorded)


@pytest.mark.asyncio
async def test_replaying_an_abandoned_event_settles_it_and_never_reexecutes(communication):  # noqa: F811
    c = communication
    repository = short(c)
    status, _ = await repository.begin("tenant", "owner", c.event)
    assert status == "started"
    await expire(c, repository)
    status, recorded = await repository.begin("tenant", "owner", c.event)
    assert status == "replay" and recorded["code"] == "interrupted"
    using(c, repository)
    # A reconnecting client resending the same event gets the recorded result, not a rerun.
    socket = Socket(c.app)
    await socket.input.put({"type": "authenticate", "ticket": await issue(c)})
    task = asyncio.create_task(communication_socket(socket))
    try:
        assert (await socket.read())["type"] == "session.ready"
        await socket.input.put(c.event.model_dump())
        assert await socket.read() == recorded
        assert c.calls == []
    finally:
        await socket.input.put(None)
        await asyncio.wait_for(task, 2)


@pytest.mark.asyncio
async def test_stale_worker_cannot_overwrite_a_newer_attempt(communication):  # noqa: F811
    c = communication
    repository = short(c)
    stale_event = event(c, "stale")
    _, stale = await repository.begin("tenant", "owner", stale_event)
    await expire(c, repository)
    _, newer = await repository.begin("tenant", "owner", event(c, "newer"))
    # The expired worker continues and tries to commit journal and transcript.
    with pytest.raises(TurnOwnershipLost):
        await repository.finish(
            "tenant",
            "owner",
            stale_event,
            {"type": "chat.context", "results": []},
            lease=stale,
            messages=(message(c, "stale"),),
            expected_document=c.conversation,
        )
    assert not await repository.renew(stale)
    assert await repository.read_messages("tenant", c.conversation.id) == ()
    assert (await repository.begin("tenant", "owner", stale_event))[1]["code"] == "interrupted"
    # The newer attempt is unaffected and still commits exactly once.
    assert await repository.renew(newer)
    await repository.finish(
        "tenant",
        "owner",
        event(c, "newer"),
        {"type": "done"},
        lease=newer,
        messages=(message(c, "newer"),),
        expected_document=c.conversation,
    )
    assert [m.event_id for m in await repository.read_messages("tenant", c.conversation.id)] == [
        "newer"
    ]
    with pytest.raises(ValueError):  # A lease can never commit another event's result.
        await repository.finish("tenant", "owner", stale_event, {}, lease=newer)


@pytest.mark.asyncio
async def test_lease_renewal_racing_with_takeover(communication):  # noqa: F811
    c = communication
    repository = short(c)
    for round_ in range(10):
        own = event(c, f"own-{round_}")
        status, lease = await repository.begin("tenant", "owner", own)
        assert status == "started"
        await expire(c, repository)
        renewed, (status, taker) = await asyncio.gather(
            repository.renew(lease), repository.begin("tenant", "owner", event(c, f"take-{round_}"))
        )
        # An expired lease can never be resurrected, whichever script runs first.
        assert renewed is False and status == "started"
        assert (await repository.begin("tenant", "owner", own))[1]["code"] == "interrupted"
        await repository.finish(
            "tenant", "owner", event(c, f"take-{round_}"), {"type": "done"}, lease=taker
        )
    # A live lease renewed concurrently with a takeover attempt keeps ownership.
    status, live = await repository.begin("tenant", "owner", event(c, "live"))
    renewed, (taken, _) = await asyncio.gather(
        repository.renew(live), repository.begin("tenant", "owner", event(c, "intruder"))
    )
    assert renewed is True and taken == "busy"


@pytest.mark.asyncio
async def test_legacy_unleased_lock_is_recovered(communication):  # noqa: F811
    c = communication
    repository = short(c)
    keys = repository._keys("tenant", c.conversation.id)
    redis = c.s.store._repo.redis
    identity = json.dumps(["owner", "legacy"])
    legacy = event(c, "legacy")
    await redis.hset(keys[1], identity, json.dumps({"digest": "x", "request": legacy.model_dump()}))
    await redis.set(keys[2], identity)  # Pre-CONV-REC lock: no TTL, no fence.
    status, _ = await repository.begin("tenant", "owner", event(c, "after"))
    assert status == "started"
    recovered = json.loads(json.loads(await redis.hget(keys[1], identity))["response"])
    assert recovered["code"] == "interrupted" and recovered["correlation_id"] == "legacy"


@pytest.mark.asyncio
async def test_heartbeat_keeps_a_long_turn_owned(communication):  # noqa: F811
    c = communication
    repository = short(c, max_turn_ms=5_000)
    dispatcher = using(c, repository)
    c.block[0] = True
    async with c.s.sessions.authenticated(c.s.token) as session:
        turn = asyncio.create_task(dispatcher.dispatch(session, c.event))
        await asyncio.wait_for(c.entered.wait(), 2)
        await asyncio.sleep(3 * LEASE_MS / 1000)  # Several lease periods.
        busy = await dispatcher.dispatch(session, event(c, "other"))
        assert busy["code"] == "busy"
        c.release.set()
        result = await asyncio.wait_for(turn, 3)
    assert result["type"] == "chat.context"
    assert (await repository.begin("tenant", "owner", c.event)) == ("replay", result)


@pytest.mark.asyncio
async def test_worker_that_loses_its_lease_stops_and_returns_the_recorded_result(communication):  # noqa: F811
    c = communication
    repository = short(c)
    dispatcher = using(c, repository)
    c.block[0] = True
    async with c.s.sessions.authenticated(c.s.token) as session:
        turn = asyncio.create_task(dispatcher.dispatch(session, c.event))
        await asyncio.wait_for(c.entered.wait(), 2)
        # Another process takes over after the lease is lost (e.g. a long pause).
        await expire(c, repository)
        status, newer = await repository.begin("tenant", "owner", event(c, "takeover"))
        assert status == "started"
        result = await asyncio.wait_for(turn, 3)
        # The stale worker was stopped; the journal's recorded result is returned.
        assert result["code"] == "interrupted" and result["correlation_id"] == c.event.event_id
        assert c.active[0] == 0
        await repository.finish(
            "tenant", "owner", event(c, "takeover"), {"type": "done"}, lease=newer
        )
    assert await repository.read_messages("tenant", c.conversation.id) == ()
    assert await repository.begin("tenant", "owner", c.event) == ("replay", result)


@pytest.mark.asyncio
async def test_cancellation_commits_cancelled_under_the_owned_lease(communication):  # noqa: F811
    c = communication
    repository = short(c)
    dispatcher = using(c, repository)
    c.block[0] = True
    async with c.s.sessions.authenticated(c.s.token) as session:
        turn = asyncio.create_task(dispatcher.dispatch(session, c.event))
        await asyncio.wait_for(c.entered.wait(), 2)
        turn.cancel()
        with pytest.raises(asyncio.CancelledError):
            await turn
    assert c.active[0] == 0
    status, recorded = await repository.begin("tenant", "owner", c.event)
    assert status == "replay" and recorded["code"] == "cancelled"
    # The lease was released by the commit: the conversation is immediately usable.
    assert (await repository.begin("tenant", "owner", event(c, "next")))[0] == "started"


@pytest.mark.asyncio
async def test_socket_disconnect_then_reconnect_replays_the_settled_turn(communication):  # noqa: F811
    c = communication
    repository = short(c)
    using(c, repository)
    c.block[0] = True
    socket = Socket(c.app)
    await socket.input.put({"type": "authenticate", "ticket": await issue(c)})
    task = asyncio.create_task(communication_socket(socket))
    assert (await socket.read())["type"] == "session.ready"
    await socket.input.put(c.event.model_dump())
    await asyncio.wait_for(c.entered.wait(), 2)
    await socket.input.put(None)  # Disconnect mid-turn.
    await asyncio.wait_for(task, 3)
    c.block[0] = False
    again = Socket(c.app)
    await again.input.put({"type": "authenticate", "ticket": await issue(c)})
    task = asyncio.create_task(communication_socket(again))
    try:
        assert (await again.read())["type"] == "session.ready"
        await again.input.put(c.event.model_dump())
        replayed = await again.read()
        assert replayed["code"] == "cancelled" and replayed["correlation_id"] == c.event.event_id
        assert len(c.calls) == 1  # Not re-executed.
    finally:
        await again.input.put(None)
        await asyncio.wait_for(task, 2)
