from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

logger = logging.getLogger(__name__)

# ---------------- settings / repo ----------------


def get_settings(request: Any):
    try:
        return request.app.state.settings
    except Exception:
        from src.base.config.config import settings as global_settings

        return global_settings


def get_cache_repo(request: Any):
    try:
        return request.app.state.cognitive_modules["cache"]["redis"]
    except Exception as e:
        raise RuntimeError("[AV cache helpers] Redis repository not available") from e


# ---------------- key primitives ----------------


def _parts(*xs: str) -> str:
    return ":".join([p for p in xs if p])


def _tenant(enabled: bool, tenant_id: str | None) -> str:
    return tenant_id if (enabled and tenant_id) else "public"


def _base_ns(cache_cfg, scope: str, tenant_id: str | None) -> str:
    mt_map = {
        "agent": cache_cfg.redis_agent_key_multi_tenant,
        "history": cache_cfg.redis_history_key_multi_tenant,
        "session": cache_cfg.redis_session_key_multi_tenant,
    }
    tenant = _tenant(mt_map.get(scope, False), tenant_id)
    return _parts(cache_cfg.redis_prefix.rstrip(":"), cache_cfg.redis_key_ns, tenant)


# ---------------- public keys / ttls ------------


def agent_spawn_key(
    request,
    user_id: str,
    agent_id: str,
    tenant_id: str | None = None,
    session_id: str | None = None,
) -> str:
    cfg = get_settings(request).cache
    base = _base_ns(cfg, "agent", tenant_id)
    return _parts(
        base,
        f"agent:v{cfg.redis_agent_key_version}",
        user_id,
        agent_id,
        session_id or "default",
        "spawned",
    )


def agent_state_key(
    request, user_id: str, agent_id: str, name: str, tenant_id: str | None = None
) -> str:
    cfg = get_settings(request).cache
    base = _base_ns(cfg, "agent", tenant_id)
    return _parts(base, f"agent:v{cfg.redis_agent_key_version}", user_id, agent_id, "state", name)


def history_key(
    request,
    user_id: str,
    agent_id: str,
    session_id: str | None = None,
    tenant_id: str | None = None,
    tool: str | None = None,
) -> str:
    """
    Per-user/agent/session history list key.
    If `tool` is provided, we suffix ':tool:{tool}' to segment a per-tool stream.
    """
    cfg = get_settings(request).cache
    base = _base_ns(cfg, "history", tenant_id)
    parts = [
        base,
        f"history:v{cfg.redis_history_key_version}",
        user_id,
        agent_id,
        session_id or "default",
    ]
    if tool:
        parts += ["tool", tool]
    return ":".join(parts)


def memory_key(
    request,
    user_id: str,
    agent_id: str,
    name: str,
    tenant_id: str | None = None,
    session_id: str | None = None,
) -> str:
    cfg = get_settings(request).cache
    base = _base_ns(cfg, "history", tenant_id)  # keep or split if you add a separate toggle
    return _parts(
        base,
        f"mem:v{cfg.redis_memory_key_version}",
        user_id,
        agent_id,
        session_id or "default",
        name,
    )


def spawn_ttl(request) -> int:
    return get_settings(request).cache.redis_agent_spawn_ttl


def history_ttl(request) -> int:
    return get_settings(request).cache.redis_history_ttl


def history_max_items(request) -> int:
    return get_settings(request).cache.redis_history_max_items


def memory_ttl(request) -> int | None:
    """
    TTL for durable key-value memory. Return None to mean 'no expiration'.
    Read from settings.cache.redis_memory_ttl if present; otherwise None.
    """
    try:
        ttl = getattr(get_settings(request).cache, "redis_memory_ttl", None)
        # normalize: only positive ints are valid; anything else => no expiry
        if isinstance(ttl, int) and ttl > 0:
            return ttl
        return None
    except Exception:
        return None


async def touch_spawn(
    request,
    user_id: str,
    agent_id: str,
    tenant_id: str | None = None,
    session_id: str | None = None,
):
    repo = get_cache_repo(request)
    await repo.set(
        agent_spawn_key(request, user_id, agent_id, tenant_id, session_id),
        "true",
        expiration=spawn_ttl(request),
    )

def _browser_session_base_ns(
    request: Any,
    tenant_id: str | None = None,
) -> str:
    cfg = get_settings(request).cache

    multi_tenant = getattr(
        cfg,
        "redis_browser_session_key_multi_tenant",
        getattr(cfg, "redis_session_key_multi_tenant", False),
    )

    tenant = _tenant(multi_tenant, tenant_id)

    return _parts(
        cfg.redis_prefix.rstrip(":"),
        cfg.redis_key_ns,
        tenant,
    )


def browser_session_key_version(request: Any) -> int:
    cfg = get_settings(request).cache
    return int(getattr(cfg, "redis_browser_session_key_version", 1))


def browser_session_ttl(request: Any) -> int:
    cfg = get_settings(request).cache
    return int(getattr(cfg, "redis_browser_session_ttl", 1800))

# ---------------- browser session --------

def browser_session_state_key(
    request: Any,
    *,
    user_id: str,
    browser_session_id: str,
    tenant_id: str | None = None,
) -> str:
    version = browser_session_key_version(request)

    return _parts(
        _browser_session_base_ns(request, tenant_id),
        f"browser_session:v{version}",
        user_id,
        browser_session_id,
        "state",
    )


def browser_session_meta_key(
    request: Any,
    *,
    user_id: str,
    browser_session_id: str,
    tenant_id: str | None = None,
) -> str:
    version = browser_session_key_version(request)

    return _parts(
        _browser_session_base_ns(request, tenant_id),
        f"browser_session:v{version}",
        user_id,
        browser_session_id,
        "meta",
    )


# ---------------- distributed spawn lock --------


def spawn_lock_key(
    request,
    user_id: str,
    agent_id: str,
    tenant_id: str | None = None,
    session_id: str | None = None,
) -> str:
    cfg = get_settings(request).cache
    base = _base_ns(cfg, "agent", tenant_id)  # <-- FIX: pass cfg, not request
    return _parts(
        base,
        f"agent:v{cfg.redis_agent_key_version}",
        user_id,
        agent_id,
        session_id or "default",
        "spawn_lock",
    )


def spawn_lock_ttl(request) -> int:
    # you can add to settings later; 30s is usually plenty
    return 30


# in-process lock to reduce Redis contention
_local_locks: dict[str, asyncio.Lock] = {}


def _local_lock_for(key: str) -> asyncio.Lock:
    lock = _local_locks.get(key)
    if lock is None:
        lock = _local_locks.setdefault(key, asyncio.Lock())
    return lock


# portable SET NX helper
async def _redis_set_nx(repo, key: str, value: str, ttl: int) -> bool:
    # single, preferred path: your repo.set supports nx and ex
    return bool(await repo.set(key, value, expiration=int(ttl), nx=True))


_UNLOCK_LUA = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
  return redis.call("DEL", KEYS[1])
else
  return 0
end
"""


async def _redis_unlock_if_owner(repo, key: str, token: str):
    # use repo.eval; fall back to raw client if you expose it
    await repo.eval(_UNLOCK_LUA, keys=[key], args=[token])


@asynccontextmanager
async def acquire_spawn_lock(
    request,
    user_id: str,
    agent_id: str,
    tenant_id: str | None = None,
    session_id: str | None = None,
    wait_timeout: float = 5.0,
    poll: float = 0.1,
    auto_renew: bool = True,
):
    repo = get_cache_repo(request)
    key = spawn_lock_key(request, user_id, agent_id, tenant_id, session_id)
    token = str(uuid.uuid4())
    ttl = spawn_lock_ttl(request)

    local = _local_lock_for(key)
    async with local:
        start = time.monotonic()
        acquired = await _redis_set_nx(repo, key, token, ttl)
        while not acquired and (time.monotonic() - start) < wait_timeout:
            await asyncio.sleep(poll + random.uniform(0, poll * 0.2))  # jitter
            acquired = await _redis_set_nx(repo, key, token, ttl)
        if not acquired:
            raise TimeoutError(f"spawn lock busy for {user_id}/{agent_id}")

        # Optional renewer if build can exceed TTL
        stop = asyncio.Event()
        renew_task = None
        if auto_renew and ttl >= 2:

            async def _renew():
                interval = max(1.0, ttl * 0.5)
                PEXPIRE_LUA = """
                if redis.call('GET', KEYS[1]) == ARGV[1] then
                  return redis.call('PEXPIRE', KEYS[1], ARGV[2])
                else
                  return 0
                end
                """
                while not stop.is_set():
                    await asyncio.sleep(interval)
                    await repo.eval(PEXPIRE_LUA, keys=[key], args=[token, int(ttl * 1000)])

            renew_task = asyncio.create_task(_renew())

        try:
            yield
        finally:
            try:
                stop.set()
                if renew_task:
                    renew_task.cancel()
            except Exception:
                pass
            await _redis_unlock_if_owner(repo, key, token)


async def invalidate_agent(request, agent_id: str) -> None:
    """
    Evict all cached instances of an agent across all users.
    Forces re-spawn from DB on next access.
    """
    try:
        registry = request.app.container.agentverse.instance_registry()
    except Exception:
        logger.warning("[invalidate_agent] instance_registry not available")
        return

    for user_id, aid, session_id in tuple(registry.keys()):
        if aid == agent_id:
            inst = registry.get(user_id, aid, session_id)
            if inst:
                try:
                    await inst.sleep(request)
                except Exception as e:
                    logger.warning("[invalidate_agent] sleep failed for %s/%s: %s", user_id, aid, e)
            registry.remove(user_id, aid, session_id)


def presence_sockets_key(
    request,
    user_id: str,
    agent_id: str,
    tenant_id: str | None = None,
    session_id: str | None = None,
) -> str:
    cfg = get_settings(request).cache
    base = _base_ns(cfg, "agent", tenant_id)
    return _parts(
        base,
        f"presence:v{cfg.redis_agent_key_version}",
        user_id,
        agent_id,
        session_id or "default",
        "sockets",
    )


def presence_ttl(request) -> int:
    # how long a socket presence survives without heartbeats (seconds)
    return 90  # tweak as needed


async def presence_join(
    request,
    user_id: str,
    agent_id: str,
    socket_id: str,
    tenant_id: str | None = None,
    session_id: str | None = None,
):
    repo = get_cache_repo(request)
    key = presence_sockets_key(request, user_id, agent_id, tenant_id, session_id)
    await repo.sadd(key, socket_id)
    await repo.expire(key, presence_ttl(request))  # initial TTL


async def presence_heartbeat(
    request,
    user_id: str,
    agent_id: str,
    tenant_id: str | None = None,
    session_id: str | None = None,
):
    repo = get_cache_repo(request)
    key = presence_sockets_key(request, user_id, agent_id, tenant_id, session_id)
    # touch the TTL so abrupt network blips don’t kill presence immediately
    await repo.expire(key, presence_ttl(request))


async def presence_leave(
    request,
    user_id: str,
    agent_id: str,
    socket_id: str,
    tenant_id: str | None = None,
    session_id: str | None = None,
) -> int:
    """Remove socket_id. Returns remaining connection count."""
    repo = get_cache_repo(request)
    key = presence_sockets_key(request, user_id, agent_id, tenant_id, session_id)
    await repo.srem(key, socket_id)
    n = await repo.scard(key)
    if n == 0:
        # optional: remove the set to keep Redis clean
        await repo.delete(key)
    return n