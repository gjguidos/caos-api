"""TEST-SAFETY-0.1: integration tests can never touch live UTU authority state.

Run through scripts/test-harness/run.sh. Guard cases need no Redis. Identity and
isolation cases use the verified disposable UTU_TEST_REDIS_URL; the contaminated-Redis
case uses a separate database and keys unique to the invocation, created with SET NX and
deleted only if this invocation created them.
"""

import json
from urllib.parse import urlsplit, urlunsplit

import pytest
from redis.asyncio import Redis

from src.base.config.config import settings
from src.base.repositories.redis_impl import RedisRepositoryImpl
from src.domains.agentverse.agents.lifecycle.tests.test_utu import setup
from src.domains.agentverse.nataraja.verify import (
    UnsafeVerificationTarget,
    require_disposable_redis,
)
from src.domains.agentverse.security.revocation import RedisRevocationStore
from src.domains.agentverse.tests import utu_isolation as isolation
from src.domains.agentverse.tests.agents import test_genesis_initiation_integration as genesis
from src.domains.agentverse.tests.utu_isolation import (
    HARNESS,
    TEST_REDIS_ENV,
    TEST_REDIS_IDENTITY_ENV,
    IsolatedRevocationStore,
    declared_redis_identity,
    disposable_utu_redis_url,
    has_default_route,
    isolate_utu,
    new_run_id,
    refuse_live_authority,
    verify_disposable_redis,
)

LIVE_KEY = RedisRevocationStore._key("caos-local")


# --- 1. Disposable endpoint verification (no Redis connection) ------------------------------


def test_a_missing_test_redis_skips_instead_of_guessing(monkeypatch):
    monkeypatch.delenv(TEST_REDIS_ENV, raising=False)
    with pytest.raises(pytest.skip.Exception):
        disposable_utu_redis_url()


@pytest.mark.parametrize("url", ["unix:///tmp/redis.sock", "http://redis:6379", "redis:///0"])
def test_ambiguous_endpoints_fail_closed(monkeypatch, url):
    monkeypatch.setenv(TEST_REDIS_ENV, url)
    with pytest.raises(UnsafeVerificationTarget, match="not a verified disposable Redis"):
        disposable_utu_redis_url()


def test_the_applications_configured_redis_is_refused_on_every_database(monkeypatch):
    live = urlsplit(settings.cache.redis_url)
    for db in ("0", "15"):
        monkeypatch.setenv(TEST_REDIS_ENV, urlunsplit(live._replace(path="/" + db)))
        with pytest.raises(UnsafeVerificationTarget, match="separate disposable Redis server"):
            disposable_utu_redis_url()


def test_equivalent_spellings_of_the_live_server_are_refused():
    # Same server through another name or address (DNS only; no connection is made).
    with pytest.raises(UnsafeVerificationTarget, match="live Redis server"):
        require_disposable_redis("redis://127.0.0.1:6390/15", "redis://localhost:6390/0")
    with pytest.raises(UnsafeVerificationTarget, match="live Redis server"):
        require_disposable_redis("redis://:pw@localhost:6390/3", "redis://localhost:6390")
    # Another server (a different port, or a reserved documentation address) is accepted.
    assert require_disposable_redis("redis://localhost:6391/0", "redis://localhost:6390/0")
    assert require_disposable_redis("redis://192.0.2.10:6379/0", "redis://localhost:6379/0")


@pytest.mark.asyncio
async def test_the_genesis_fixture_refuses_live_redis_before_any_connection(monkeypatch):
    monkeypatch.setenv("OPS2_MONGO_INTEGRATION", "1")
    monkeypatch.setenv(TEST_REDIS_ENV, settings.cache.redis_url)

    def never(*args, **kwargs):
        raise AssertionError("connected to Redis before the endpoint was verified")

    monkeypatch.setattr(genesis.Redis, "from_url", never)
    with pytest.raises(UnsafeVerificationTarget):
        await genesis.build_local_app(monkeypatch)


# --- 2. Harness boundary and declared Redis process identity --------------------------------

INTERNAL_V4 = "Iface\tDestination\tGateway\tFlags\neth0\t000014AC\t00000000\t0001\n"
LO_REJECT_V6 = (
    "00000000000000000000000000000000 00 00000000000000000000000000000000 00 "
    "00000000000000000000000000000000 ffffffff 00000001 00000000 00200200 lo\n"
)


def routes(tmp_path, v4, v6=None):
    (tmp_path / "v4").write_text(v4)
    if v6 is not None:
        (tmp_path / "v6").write_text(v6)
    return str(tmp_path / "v4"), str(tmp_path / "v6")


def test_only_a_runner_without_a_default_route_counts_as_isolated(tmp_path):
    # The harness's internal network: a subnet route and IPv6 reject routes on lo only.
    assert not has_default_route(*routes(tmp_path, INTERNAL_V4, LO_REJECT_V6))
    assert not has_default_route(*routes(tmp_path, INTERNAL_V4))  # IPv6 disabled
    default_v4 = INTERNAL_V4 + "eth0\t00000000\t010014AC\t0003\n"
    assert has_default_route(*routes(tmp_path, default_v4, LO_REJECT_V6))
    default_v6 = LO_REJECT_V6.replace("00200200 lo", "00000003 eth0")
    assert has_default_route(*routes(tmp_path, INTERNAL_V4, default_v6))
    assert has_default_route(str(tmp_path / "unreadable"), str(tmp_path / "v6"))  # fail closed


def test_this_runner_is_on_the_harness_internal_network():
    assert not has_default_route(), f"run destructive integration tests through {HARNESS}"


def test_a_runner_that_can_reach_other_networks_is_refused(monkeypatch):
    monkeypatch.setattr(isolation, "has_default_route", lambda: True)
    with pytest.raises(UnsafeVerificationTarget, match="internal network"):
        disposable_utu_redis_url()


@pytest.mark.parametrize("declared", [None, "", "not-a-run-id", "A" * 40])
def test_a_missing_or_malformed_identity_declaration_is_refused(monkeypatch, declared):
    if declared is None:
        monkeypatch.delenv(TEST_REDIS_IDENTITY_ENV, raising=False)
    else:
        monkeypatch.setenv(TEST_REDIS_IDENTITY_ENV, declared)
    with pytest.raises(UnsafeVerificationTarget, match="must be declared by"):
        disposable_utu_redis_url()


@pytest.mark.asyncio
async def test_the_declared_redis_process_identity_is_required_before_any_write(monkeypatch):
    redis = Redis.from_url(disposable_utu_redis_url())
    try:
        await verify_disposable_redis(redis)  # the server the harness declared: accepted
        actual = (await redis.info("server"))["run_id"]
        assert actual == declared_redis_identity()
        other = "f" * 40 if actual != "f" * 40 else "e" * 40
        monkeypatch.setenv(TEST_REDIS_IDENTITY_ENV, other)
        with pytest.raises(UnsafeVerificationTarget, match="mismatch"):
            await verify_disposable_redis(redis)
    finally:
        await redis.aclose()


class Unreadable:
    def __init__(self, info):
        self._info = info

    async def info(self, section):
        if isinstance(self._info, Exception):
            raise self._info
        return self._info

    def scan_iter(self, **kwargs):
        raise AssertionError("never inspected or written without a proven identity")


@pytest.mark.asyncio
@pytest.mark.parametrize("info", [ConnectionError("no INFO"), {}, {"run_id": None}])
async def test_an_unreadable_identity_is_refused(info):
    with pytest.raises(UnsafeVerificationTarget):
        await verify_disposable_redis(Unreadable(info))


# --- 3. Live or contaminated content is refused before any write -----------------------------


@pytest.mark.asyncio
async def test_production_format_authority_keys_are_refused_before_any_write():
    url = disposable_utu_redis_url()
    # A separate database on the disposable server, and production-FORMAT keys unique to
    # this invocation: never the production tenant key, never another run's key.
    contaminated = Redis.from_url(urlunsplit(urlsplit(url)._replace(path="/14")))
    invocation = new_run_id()
    candidates = (
        RedisRevocationStore._key("contamination-" + invocation),  # matches caos:utu:v1:*
        "caos:{genesis-sessions}:v1:contamination-" + invocation,
    )
    owned: list[str] = []  # only keys this invocation created (SET NX) are ever deleted
    try:
        for key in candidates:
            assert await contaminated.set(key, "contamination-test", nx=True), (
                f"{key} already existed; refusing to adopt or delete state this run did not create"
            )
            owned.append(key)
            # Refused (a concurrent invocation's own key may also be present).
            with pytest.raises(UnsafeVerificationTarget, match="live or contaminated"):
                await refuse_live_authority(contaminated)
            await contaminated.delete(key)
            owned.remove(key)
    finally:
        if owned:
            await contaminated.delete(*owned)
        await contaminated.aclose()


# --- 3a. A refused invocation never deletes preexisting authority -----------------------


async def hash_snapshot(redis, key):
    return sorted((k.decode(), v.decode()) for k, v in (await redis.hgetall(key)).items())


@pytest.mark.asyncio
async def test_a_refused_invocation_keeps_another_runs_authority(monkeypatch):
    redis = Redis.from_url(disposable_utu_redis_url())
    other = IsolatedRevocationStore(repository=RedisRepositoryImpl(redis), run_id=new_run_id())
    other_key = other._key("caos-local")
    try:
        env = setup()
        env.utu._revocations = other
        await env.utu.install_policy("caos-local", expected=None, authorization=env.policy)
        await other.revoke("caos-local", session_id="other-run-session")
        before = await hash_snapshot(redis, other_key)
        monkeypatch.setenv(TEST_REDIS_IDENTITY_ENV, "e" * 40)  # not this server
        with pytest.raises(UnsafeVerificationTarget, match="mismatch"):
            await genesis.build_local_app(monkeypatch)
        assert await hash_snapshot(redis, other_key) == before  # policy, incarnation, revocations
    finally:
        await redis.delete(other_key)
        await redis.aclose()


@pytest.mark.asyncio
async def test_a_refused_invocation_keeps_preexisting_production_format_keys(monkeypatch):
    url = urlunsplit(urlsplit(disposable_utu_redis_url())._replace(path="/13"))
    redis = Redis.from_url(url)
    key = RedisRevocationStore._key("preexisting-" + new_run_id())  # caos:utu:v1:* format
    owned = False
    try:
        owned = bool(await redis.set(key, "preexisting", nx=True))
        assert owned, "the key existed already; refusing to adopt state this run did not create"
        monkeypatch.setenv(TEST_REDIS_ENV, url)
        with pytest.raises(UnsafeVerificationTarget, match="live or contaminated"):
            await genesis.build_local_app(monkeypatch)
        assert await redis.get(key) == b"preexisting"  # the refusal deleted nothing
    finally:
        if owned:
            await redis.delete(key)
        await redis.aclose()


# --- 4. Per-run isolation of the UTU tenant key ----------------------------------------------


@pytest.mark.asyncio
async def test_independent_runs_never_share_a_utu_key():
    redis = Redis.from_url(disposable_utu_redis_url())
    repository = RedisRepositoryImpl(redis)
    first = IsolatedRevocationStore(repository=repository, run_id=new_run_id())
    second = IsolatedRevocationStore(repository=repository, run_id=new_run_id())
    keys = {first._key("caos-local"), second._key("caos-local")}
    try:
        assert len(keys) == 2 and LIVE_KEY not in keys
        for store in (first, second):
            env = setup()
            env.utu._revocations = store
            await env.utu.install_policy("caos-local", expected=None, authorization=env.policy)
        await first.revoke("caos-local", session_id="only-in-first")
        before = await redis.hgetall(second._key("caos-local"))
        assert not any(b"session" in k for k in before)  # the revocation stayed in its run
        await redis.delete(first._key("caos-local"))  # one run's teardown ...
        assert await redis.hgetall(second._key("caos-local")) == before  # ... keeps the other
        assert await redis.exists(LIVE_KEY) == 0  # the production key is never written
    finally:
        await redis.delete(*keys)
        await redis.aclose()


@pytest.mark.asyncio
async def test_isolation_refuses_a_container_whose_utu_is_already_bound(monkeypatch):
    built = await genesis.build_local_app(monkeypatch)  # UTU already bound to its run
    try:
        with pytest.raises(RuntimeError, match="resolved before isolation"):
            isolate_utu(built.container, built.utu_store._repo, new_run_id())
    finally:
        await genesis.close_local_app(built)


@pytest.mark.asyncio
async def test_a_runs_startup_and_teardown_preserve_another_runs_authority(monkeypatch):
    redis = Redis.from_url(disposable_utu_redis_url())
    other = IsolatedRevocationStore(repository=RedisRepositoryImpl(redis), run_id=new_run_id())
    other_key = other._key("caos-local")

    async def snapshot(key):
        return json.dumps(
            sorted((k.decode(), v.decode()) for k, v in (await redis.hgetall(key)).items())
        )

    try:  # the other run's key is removed even if anything below fails
        env = setup()
        env.utu._revocations = other
        await env.utu.install_policy("caos-local", expected=None, authorization=env.policy)
        await other.revoke("caos-local", session_id="other-run-session")
        before = await snapshot(other_key)
        built = await genesis.build_local_app(monkeypatch)
        own_key = built.utu_store._key("caos-local")
        try:
            # The real Genesis startup (first install, pinned) uses only this run's key.
            await built.app.state.start_genesis_sessions()
            assert own_key != other_key and await redis.hget(own_key, "policy")
        finally:
            await genesis.close_local_app(built)
        assert await redis.exists(own_key) == 0  # teardown removed only its own key
        assert await redis.exists(LIVE_KEY) == 0
        # The other run's policy, incarnation and revocations are untouched.
        assert await snapshot(other_key) == before
    finally:
        await redis.delete(other_key)
        await redis.aclose()
