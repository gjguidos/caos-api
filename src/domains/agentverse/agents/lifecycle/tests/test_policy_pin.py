"""POLICY-PIN-0.1: startup accepts only the explicitly approved UTU policy.

Redis cases use the disposable UTU_TEST_REDIS_URL with a per-test key prefix (never a
shared persistent Redis, never a flush).
"""

import json
import logging
import os
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from src.base.events.v2.contracts import EventPermissionError
from src.base.lifespan.tests.test_resource_lifecycle import lifecycle  # noqa: F401
from src.base.repositories.redis_impl import RedisRepositoryImpl
from src.domains.agentverse.agents.lifecycle.tests.test_utu import KEY, setup
from src.domains.agentverse.dependencies.genesis_local import initialize_local
from src.domains.agentverse.ncsc.authorization import SynapticAuthorization, SynapticGrants
from src.domains.agentverse.security.policy_pin import (
    PIN_FILE_ENV,
    PolicyPin,
    PolicyPinError,
    enforce_policy_pin,
    load_pin,
    pin_path,
)
from src.domains.agentverse.security.revocation import RedisRevocationStore, policy_version
from src.domains.agentverse.tests.agents import test_genesis_initiation_integration as genesis

local_app = genesis.local_app
TENANT = "tenant"


def pin(
    approved: str,
    transition_from: str | None = None,
    tenant: str = TENANT,
    provision: bool = False,
) -> PolicyPin:
    return PolicyPin(
        contract="caos.utu_policy_pin",
        schema_version=1,
        tenant_id=tenant,
        approved_policy_version=approved,
        transition_from=transition_from,
        provision_unprovisioned=provision,
    )


def policy(*signals: str) -> SynapticAuthorization:
    return SynapticAuthorization(grants={(TENANT, "agent"): SynapticGrants(set(signals))})


OLD, NEW = policy("chat.respond"), policy("chat.respond", "data.validation")
V_OLD, V_NEW = policy_version(OLD), policy_version(NEW)


# --- Pin contract ------------------------------------------------------------------------


def test_the_pin_is_an_exact_strict_contract():
    assert pin(V_NEW, V_OLD).transition_from == V_OLD
    for bad in (
        {"approved_policy_version": "88cdfa80"},  # never a prefix
        {"approved_policy_version": V_NEW.upper()},
        {"transition_from": V_NEW},  # a transition must change the version
        {"tenant_id": ""},
        {"grants": []},  # the pin carries no authority
        {"provision_unprovisioned": "yes"},  # consent is an explicit boolean
    ):
        data = pin(V_NEW).model_dump() | bad
        with pytest.raises(ValueError):
            PolicyPin.model_validate(data)


def test_the_pin_lives_next_to_the_policy_unless_overridden(monkeypatch, tmp_path):
    monkeypatch.delenv(PIN_FILE_ENV, raising=False)
    assert str(pin_path("/app/config/genesis.local.json")) == "/app/config/genesis.local.pin.json"
    with pytest.raises(PolicyPinError):
        pin_path(None)  # managed local startup without a policy file has no pin
    monkeypatch.setenv(PIN_FILE_ENV, str(tmp_path / "missing.json"))
    with pytest.raises(PolicyPinError, match="required"):
        load_pin("/app/config/genesis.local.json")
    (tmp_path / "missing.json").write_text("{not json")
    with pytest.raises(PolicyPinError, match="invalid"):
        load_pin(None)


def test_the_checked_in_pin_approves_the_checked_in_policy(monkeypatch):
    monkeypatch.delenv(PIN_FILE_ENV, raising=False)
    approved, path = load_pin("config/genesis.local.json")
    assert path.name == "genesis.local.pin.json"
    assert approved.tenant_id == "caos-local" and approved.transition_from is None
    assert approved.provision_unprovisioned is False  # a managed tenant never auto-installs
    config = json.loads(Path("config/genesis.local.json").read_text())
    checked_in = SynapticAuthorization(
        grants={
            (g["tenant_id"], g["agent_id"]): SynapticGrants(g["invoke"]) for g in config["grants"]
        }
    )
    assert policy_version(checked_in) == approved.approved_policy_version


# --- Enforcement against a real, isolated Redis ---------------------------------------------


@pytest_asyncio.fixture
async def redis_env():
    url = os.environ.get("UTU_TEST_REDIS_URL")
    if not url:
        pytest.skip("Set UTU_TEST_REDIS_URL for Redis 6 integration tests")
    redis = Redis.from_url(url)
    prefix = "test:pin:" + uuid4().hex + ":"

    class IsolatedStore(RedisRevocationStore):
        @staticmethod
        def _key(tenant):
            return prefix + RedisRevocationStore._key(tenant)

    store = IsolatedStore(repository=RedisRepositoryImpl(redis), timeout=1.0)
    env = setup()
    env.utu._revocations = store
    key = IsolatedStore._key(TENANT)
    try:
        yield env, store, redis, key
    finally:
        await redis.delete(key)
        await redis.aclose()


async def state(redis, key) -> dict:
    return {k.decode(): v.decode() for k, v in (await redis.hgetall(key)).items()}


async def enforce(env, store, authorization, approved):
    return await enforce_policy_pin(
        store=store, utu=env.utu, policy=authorization, pin=approved, tenant_id=TENANT
    )


async def installed_with_revocations(env, store, redis, key, authorization):
    await env.utu.install_policy(TENANT, expected=None, authorization=authorization)
    await store.revoke(TENANT, session_id="revoked-session")
    await store.revoke(TENANT, agent_id="revoked-agent")
    return await state(redis, key)


@pytest.mark.asyncio
async def test_approved_installed_policy_starts_without_any_write(redis_env):
    env, store, redis, key = redis_env
    before = await installed_with_revocations(env, store, redis, key, NEW)
    assert await enforce(env, store, NEW, pin(V_NEW)) == "verified"
    # Byte-identical: the install prefix, tenant incarnation and revocations are kept.
    assert await state(redis, key) == before


@pytest.mark.asyncio
async def test_unapproved_source_policy_fails_without_writes(redis_env):
    env, store, redis, key = redis_env
    # Unprovisioned tenant: a mismatching source is never provisioned.
    with pytest.raises(PolicyPinError, match="not the approved pin"):
        await enforce(env, store, NEW, pin(V_OLD))
    assert await redis.exists(key) == 0
    # Provisioned tenant: the installed authority is untouched.
    before = await installed_with_revocations(env, store, redis, key, OLD)
    for approved in (pin(V_OLD), pin(V_OLD, transition_from=V_NEW)):
        with pytest.raises(PolicyPinError, match="nothing was installed"):
            await enforce(env, store, NEW, approved)
    assert await state(redis, key) == before


@pytest.mark.asyncio
async def test_unapproved_installed_policy_fails_without_writes(redis_env):
    env, store, redis, key = redis_env
    before = await installed_with_revocations(env, store, redis, key, OLD)
    other = policy_version(policy("other"))
    for approved in (pin(V_NEW), pin(V_NEW, transition_from=other)):
        with pytest.raises(PolicyPinError, match="no transition from it is authorized"):
            await enforce(env, store, NEW, approved)
    assert await state(redis, key) == before  # never overwritten automatically


@pytest.mark.asyncio
async def test_unprovisioned_tenant_requires_explicit_first_install_consent(redis_env):
    env, store, redis, key = redis_env
    with pytest.raises(PolicyPinError, match="does not authorize a first install"):
        await enforce(env, store, NEW, pin(V_NEW))
    assert await redis.exists(key) == 0
    # Consent never bypasses the tenant or source-policy checks.
    with pytest.raises(PolicyPinError, match="another tenant"):
        await enforce(env, store, NEW, pin(V_NEW, tenant="caos-local", provision=True))
    with pytest.raises(PolicyPinError, match="not the approved pin"):
        await enforce(env, store, NEW, pin(V_OLD, provision=True))
    assert await redis.exists(key) == 0
    assert await enforce(env, store, NEW, pin(V_NEW, provision=True)) == "provisioned"
    assert (await state(redis, key))["policy"].split(":")[-1] == V_NEW
    assert await enforce(env, store, NEW, pin(V_NEW, provision=True)) == "verified"


@pytest.mark.asyncio
async def test_first_install_consent_never_overwrites_an_installed_policy(redis_env):
    env, store, redis, key = redis_env
    before = await installed_with_revocations(env, store, redis, key, OLD)
    with pytest.raises(PolicyPinError, match="no transition from it is authorized"):
        await enforce(env, store, NEW, pin(V_NEW, provision=True))
    assert await state(redis, key) == before


@pytest.mark.asyncio
async def test_lost_policy_field_keeps_incarnation_and_revocations(redis_env):
    env, store, redis, key = redis_env
    before = await installed_with_revocations(env, store, redis, key, NEW)
    await redis.hdel(key, "policy")  # partial loss: the incarnation and revocations remain
    remaining = await state(redis, key)
    assert "policy" not in remaining and remaining["tenant"] == before["tenant"]
    # Without consent a managed tenant refuses to start (and writes nothing).
    with pytest.raises(PolicyPinError, match="does not authorize a first install"):
        await enforce(env, store, NEW, pin(V_NEW))
    assert await state(redis, key) == remaining
    # With explicit consent only the policy field is restored; the tenant incarnation and
    # every revocation are kept, and the install prefix is new (old permits cannot survive).
    assert await enforce(env, store, NEW, pin(V_NEW, provision=True)) == "provisioned"
    after = await state(redis, key)
    assert {k: v for k, v in after.items() if k != "policy"} == remaining
    assert after["policy"].split(":")[-1] == V_NEW and after["policy"] != before["policy"]


@pytest.mark.asyncio
async def test_a_revoked_tenant_stays_revoked_across_an_approved_transition(redis_env):
    env, store, redis, key = redis_env
    await installed_with_revocations(env, store, redis, key, OLD)
    await store.revoke(TENANT)  # the tenant kill switch
    assert (await state(redis, key))["tenant"] == "revoked"
    assert await enforce(env, store, NEW, pin(V_NEW, transition_from=V_OLD)) == "transitioned"
    after = await state(redis, key)
    assert after["tenant"] == "revoked" and after["policy"].split(":")[-1] == V_NEW
    with pytest.raises(EventPermissionError):
        await store.read(KEY)  # every permit is still denied


@pytest.mark.asyncio
async def test_outcomes_are_logged_with_tenant_and_short_digests(redis_env, caplog):
    env, store, _, _ = redis_env
    caplog.set_level(logging.INFO, logger="src.domains.agentverse.security.policy_pin")
    await enforce(env, store, OLD, pin(V_OLD, provision=True))
    await enforce(env, store, NEW, pin(V_NEW, transition_from=V_OLD))
    await enforce(env, store, NEW, pin(V_NEW))
    messages = [r.getMessage() for r in caplog.records if r.name.endswith("policy_pin")]
    assert [m.split(":")[0] for m in messages] == [
        "UTU policy pin provisioned",
        "UTU policy pin transitioned",
        "UTU policy pin verified",
    ]
    assert f"installed={V_OLD[:12]}… approved={V_NEW[:12]}…" in messages[1]
    assert all(f"tenant={TENANT}" in m for m in messages)
    # Only short digests: never the stored value (its install prefix) or a full digest.
    raw = await store.current_policy(TENANT)
    assert not any(raw in m or V_NEW in m or V_OLD in m for m in messages)


@pytest.mark.asyncio
async def test_explicit_transition_preserves_incarnation_and_revocations(redis_env):
    env, store, redis, key = redis_env
    before = await installed_with_revocations(env, store, redis, key, OLD)
    assert await enforce(env, store, NEW, pin(V_NEW, transition_from=V_OLD)) == "transitioned"
    after = await state(redis, key)
    assert after["policy"].split(":")[-1] == V_NEW and after["policy"] != before["policy"]
    # Same tenant incarnation; every revocation survives the transition.
    assert {k: v for k, v in after.items() if k != "policy"} == {
        k: v for k, v in before.items() if k != "policy"
    }
    # A stale pin left in place authorizes nothing further: the next start only verifies.
    assert await enforce(env, store, NEW, pin(V_NEW, transition_from=V_OLD)) == "verified"
    assert await state(redis, key) == after
    # Rollback is another explicit transition, never automatic.
    with pytest.raises(PolicyPinError):
        await enforce(env, store, OLD, pin(V_OLD))
    assert await enforce(env, store, OLD, pin(V_OLD, transition_from=V_NEW)) == "transitioned"


@pytest.mark.asyncio
async def test_a_raced_transition_leaves_the_prior_authority_intact(redis_env):
    env, store, redis, key = redis_env
    await installed_with_revocations(env, store, redis, key, OLD)
    stale = await store.current_policy(TENANT)
    # Another writer installs between this startup's read and its compare-and-set.
    await env.utu.install_policy(TENANT, expected=stale, authorization=OLD)
    concurrent = await state(redis, key)

    class StaleRead:
        async def current_policy(self, tenant_id):
            return stale

    with pytest.raises(PolicyPinError, match="did not complete.*prior authority is kept"):
        await enforce_policy_pin(
            store=StaleRead(),
            utu=env.utu,
            policy=NEW,
            pin=pin(V_NEW, transition_from=V_OLD),
            tenant_id=TENANT,
        )
    assert await state(redis, key) == concurrent


@pytest.mark.asyncio
async def test_a_pin_for_another_tenant_authorizes_nothing(redis_env):
    env, store, redis, key = redis_env
    with pytest.raises(PolicyPinError, match="another tenant"):
        await enforce(env, store, NEW, pin(V_NEW, tenant="caos-local"))
    assert await redis.exists(key) == 0


# --- Managed local startup and reload (real application wiring) -----------------------------


@pytest.mark.asyncio
async def test_every_startup_or_reload_re_reads_and_enforces_the_pin(
    local_app, monkeypatch, tmp_path
):
    s = local_app  # started with config/genesis.local.json and its checked-in pin
    utu = s.av.utu_authority()
    key = RedisRevocationStore._key("caos-local")
    redis = utu._store()._repo.redis
    installed = await redis.hgetall(key)
    approved = json.loads(Path("config/genesis.local.pin.json").read_text())
    pin_file = tmp_path / "pin.json"
    monkeypatch.setenv(PIN_FILE_ENV, str(pin_file))
    # A reload re-runs local initialization: a missing pin fails closed ...
    with pytest.raises(PolicyPinError, match="required"):
        await initialize_local(s.app.container)
    # ... as does an edited pin that no longer approves the checked-in policy ...
    pin_file.write_text(json.dumps(approved | {"approved_policy_version": V_NEW}))
    with pytest.raises(PolicyPinError, match="not the approved pin"):
        await initialize_local(s.app.container)
    assert await redis.hgetall(key) == installed  # no policy write on refusal
    # ... and the unchanged approved pin starts normally, still without writing.
    pin_file.write_text(json.dumps(approved))
    await initialize_local(s.app.container)
    assert await redis.hgetall(key) == installed


@pytest.mark.asyncio
@pytest.mark.parametrize("refusal", ["checked_in_pin_on_fresh_tenant", "unapproved_source"])
async def test_a_refused_pin_aborts_the_whole_application_startup(
    lifecycle,  # noqa: F811 - the shared real-lifespan fixture
    monkeypatch,
    refusal,
):
    built = await genesis.build_local_app(monkeypatch)  # real Genesis wiring, not started
    key = RedisRevocationStore._key("caos-local")
    try:
        if refusal == "checked_in_pin_on_fresh_tenant":
            monkeypatch.delenv(PIN_FILE_ENV)  # the managed pin never provisions
        else:
            approved = json.loads(Path("config/genesis.local.pin.json").read_text())
            Path(os.environ[PIN_FILE_ENV]).write_text(
                json.dumps(approved | {"approved_policy_version": V_NEW})
            )
        # The real lifespan runs the real Genesis start hook, exactly as the API does.
        lifecycle.app.state.start_genesis_sessions = built.app.state.start_genesis_sessions
        lifecycle.app.state.close_genesis_sessions = built.app.state.close_genesis_sessions
        with pytest.raises(PolicyPinError):
            async with lifecycle.module.lifespan(lifecycle.app):
                pytest.fail("startup must not complete")
        assert "rabbitmq.connect" not in lifecycle.trace  # nothing after the guard started
        assert "redis.exit" in lifecycle.trace  # acquired resources were cleaned up
        assert await built.redis.exists(key) == 0  # no policy was installed
    finally:
        await genesis.close_local_app(built)
