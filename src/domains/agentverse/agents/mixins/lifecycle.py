from __future__ import annotations

import logging
from typing import Any

from src.domains.agentverse.cache import helpers as av_cache

logger = logging.getLogger(__name__)


class LifecycleMixin:
    def bind_runtime(
        self, *, request=None, default_session_id: str | None = None, tenant_id: str | None = None
    ):
        self.request = request
        if default_session_id is not None:
            self.default_session_id = default_session_id
        self.tenant_id = tenant_id

    async def mark_spawned(self, request: Any | None = None, tenant_id: str | None = None) -> None:
        req = request or self.request
        self.request = req or self.request
        self.spawned = True
        effective_tenant_id = tenant_id or self.tenant_id
        session_id = self.default_session_id
        try:
            repo = av_cache.get_cache_repo(req) or self.cache
            if repo:
                key = av_cache.agent_spawn_key(
                    req,
                    self.user_id,
                    self.id,
                    effective_tenant_id,
                    session_id,
                )
                await repo.set(key, "true", expiration=av_cache.spawn_ttl(req))
        except Exception as e:
            logger.warning("[mark_spawned] Failed to set spawn bit: %s", e)

        try:
            reg = req.app.container.agentverse.instance_registry
            reg.set(self.user_id, self.id, self, session_id)
        except Exception as e:
            logger.debug("[mark_spawned] No runtime registry available: %s", e)

    async def sleep(self, request=None, tenant_id: str | None = None) -> None:
        self.spawned = False
        effective_tenant_id = tenant_id or self.tenant_id
        session_id = self.default_session_id
        try:
            repo = av_cache.get_cache_repo(request) if request else self.cache
            if repo:
                key = (
                    av_cache.agent_spawn_key(
                        request,
                        self.user_id,
                        self.id,
                        effective_tenant_id,
                        session_id,
                    )
                    if request
                    else f"agent:{self.id}:{session_id or 'default'}:spawned"
                )
                await repo.delete(key)
        except Exception as e:
            logger.warning("[sleep] failed clearing Redis spawn flag: %s", e)

        try:
            if request and hasattr(request.app, "container"):
                runtime_reg = getattr(request.app.container.agentverse, "instance_registry", None)
                if runtime_reg:
                    runtime_reg.remove(self.user_id, self.id, session_id)
        except Exception as e:
            logger.warning("[sleep] failed removing from runtime registry: %s", e)

    async def is_spawned(self, request: Any | None = None, tenant_id: str | None = None) -> bool:
        req = request or self.request
        repo = av_cache.get_cache_repo(req) or self.cache
        if not repo:
            return getattr(self, "spawned", False)
        key = av_cache.agent_spawn_key(
            req,
            self.user_id,
            self.id,
            tenant_id or self.tenant_id,
            self.default_session_id,
        )
        return (await repo.get(key)) == "true"
