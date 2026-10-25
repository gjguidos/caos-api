from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.runtime.instances_v2 import (
    AgentInstanceKey,
    AgentInstanceRegistryV2,
)
from src.domains.agentverse.runtime.presence import (
    AgentLease,
    AgentPresenceStore,
)
from src.domains.agentverse.security.revocation import ExecutionPermit
from src.domains.agentverse.security.utu import LifecycleOperation, UTUAuthority

from .models import (
    AgentLifecycleResult,
    AgentLifecycleState,
    ManagedAgentFactory,
    ManagedAgentRuntime,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _ActiveAgent:
    runtime: ManagedAgentRuntime
    lease: AgentLease
    permit: ExecutionPermit
    heartbeat: asyncio.Task[None] | None = None
    stopping: bool = False


class AgentLifecycleService:
    """
    Shared lifecycle coordinator for Khepri and Anubis.

    A single instance is required per local runtime registry.
    """

    def __init__(
        self,
        *,
        instances: AgentInstanceRegistryV2,
        presence: AgentPresenceStore,
        factory: ManagedAgentFactory,
        runtime_id: str,
        heartbeat_interval: float = 15.0,
        authority: UTUAuthority | None = None,
    ) -> None:
        if not runtime_id:
            raise ValueError("runtime_id is required")
        if heartbeat_interval <= 0:
            raise ValueError("heartbeat_interval must be positive")

        self._authority = authority
        self._instances = instances
        self._presence = presence
        self._factory = factory
        self._runtime_id = runtime_id
        self._heartbeat_interval = heartbeat_interval
        self._active: dict[AgentInstanceKey, _ActiveAgent] = {}
        self._failed: set[AgentInstanceKey] = set()
        self._starting: dict[AgentInstanceKey, asyncio.Task[Any]] = {}
        self._cleanup_tasks: set[asyncio.Task[None]] = set()
        if authority is not None:
            authority.subscribe_revocation(self.revoke_local)

    def revoke_local(
        self, tenant_id: str, agent_id: str | None = None, session_id: str | None = None
    ) -> None:
        """Trusted Anubis path: close gates synchronously, then supervise cleanup."""
        for key, task in tuple(self._starting.items()):
            if UTUAuthority.matches(key, tenant_id, agent_id, session_id):
                task.cancel()
        for key, entry in tuple(self._active.items()):
            if UTUAuthority.matches(key, tenant_id, agent_id, session_id):
                entry.runtime.revoke()
                self._schedule_cleanup(key)

    def _schedule_cleanup(self, key: AgentInstanceKey) -> None:
        task = asyncio.create_task(self._cleanup_lost_runtime(key))
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    async def _authorize(self, key: AgentInstanceKey, operation: LifecycleOperation) -> None:
        if self._authority is None:
            raise EventPermissionError("UTU lifecycle authority is not bound")
        await self._authority.authorize_lifecycle(key, operation=operation)

    async def activate(
        self,
        key: AgentInstanceKey,
    ) -> AgentLifecycleResult:
        async with self._instances.lock(key):
            if self._authority is None:
                raise EventPermissionError("UTU lifecycle authority is not bound")
            principal = await self._authority.resolve_agent_principal(key)
            permit = principal.permit
            current = self._active.get(key)

            if current is not None:
                if not await self._presence.is_owner(
                    key,
                    lease=current.lease,
                ):
                    raise RuntimeError("Local agent has lost Redis ownership")

                if key in self._failed:
                    raise RuntimeError("Agent runtime requires recovery")

                return AgentLifecycleResult(
                    key=key,
                    state=AgentLifecycleState.ACTIVE,
                    already_active=True,
                )

            if self._instances.exists(key):
                raise RuntimeError("Registry contains an unmanaged agent instance")

            lease = await self._presence.claim(
                key,
                runtime_id=self._runtime_id,
            )

            if lease is None:
                raise RuntimeError("Agent runtime is already owned")

            runtime: ManagedAgentRuntime | None = None
            activation_task = asyncio.current_task()
            assert activation_task is not None
            self._starting[key] = activation_task
            activation_guard = asyncio.create_task(
                self._guard_activation(key, lease, permit, activation_task)
            )

            try:
                await self._authority.check_permit(permit)
                runtime = await self._factory.build(key)

                if (
                    runtime.id != key.agent_id
                    or runtime.user_id != key.user_id
                    or runtime.tenant_id != key.tenant_id
                ):
                    raise ValueError("Factory returned an invalid agent identity")

                entry = _ActiveAgent(runtime=runtime, lease=lease, permit=permit)
                self._active[key] = entry
                entry.heartbeat = asyncio.create_task(self._keep_alive(key, entry))
                activation_guard.cancel()
                await self._authority.check_permit(permit)
                await runtime.connect()
                await self._authority.check_permit(permit)
                await runtime.start()
                await self._authority.check_permit(permit)

                if not await self._presence.is_owner(
                    key,
                    lease=lease,
                ):
                    raise RuntimeError("Activation lease expired")

                await self._authority.check_permit(permit)
                self._instances.register(key, runtime)

                self._failed.discard(key)

                return AgentLifecycleResult(
                    key=key,
                    state=AgentLifecycleState.ACTIVE,
                )

            except BaseException:
                if runtime is not None:
                    # Keep tracking and renewing ownership if stop cannot prove quiescence.
                    if key not in self._active:
                        entry = _ActiveAgent(runtime=runtime, lease=lease, permit=permit)
                        self._active[key] = entry
                        entry.heartbeat = asyncio.create_task(self._keep_alive(key, entry))
                    try:
                        await self._deactivate_locked(key)
                    except BaseException:
                        self._failed.add(key)
                        logger.exception("Agent cleanup failed after activation error")
                else:
                    await self._presence.release(key, lease=lease)
                raise
            finally:
                activation_guard.cancel()
                await asyncio.gather(activation_guard, return_exceptions=True)
                self._starting.pop(key, None)

    async def _guard_activation(
        self,
        key: AgentInstanceKey,
        lease: AgentLease,
        permit: ExecutionPermit,
        task: asyncio.Task[Any],
    ) -> None:
        """Renew ownership even while factory construction is awaiting/cancelling."""
        assert self._authority is not None
        cancelled = False
        try:
            while True:
                await asyncio.sleep(min(self._heartbeat_interval, self._authority.poll_interval))
                try:
                    await self._authority.check_permit(permit)
                except EventPermissionError:
                    if not cancelled:
                        task.cancel()
                        cancelled = True
                async with asyncio.timeout(1.0):
                    owned = await self._presence.renew(key, lease=lease)
                if not owned:
                    task.cancel()
                    return
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Activation lease guard failed")
            task.cancel()

    async def deactivate(
        self,
        key: AgentInstanceKey,
    ) -> AgentLifecycleResult:
        async with self._instances.lock(key):
            await self._authorize(key, "deactivate")
            return await self._deactivate_locked(key)

    async def _deactivate(self, key: AgentInstanceKey) -> AgentLifecycleResult:
        """Trusted shutdown/lost-lease cleanup; never expose as an API dependency."""
        async with self._instances.lock(key):
            return await self._deactivate_locked(key)

    async def _deactivate_locked(self, key: AgentInstanceKey) -> AgentLifecycleResult:
        """Caller holds the instance lock and has established authority."""
        entry = self._active.get(key)

        if entry is None:
            if self._instances.exists(key):
                raise RuntimeError("Unmanaged instance requires reconciliation")

            return AgentLifecycleResult(
                key=key,
                state=AgentLifecycleState.SLEEPING,
            )

        entry.stopping = True
        entry.runtime.revoke()

        try:
            await entry.runtime.stop()
        except BaseException:
            self._failed.add(key)
            logger.exception(
                "Anubis could not stop agent %s",
                key.agent_id,
            )
            # Keep the registered instance and ownership record.
            # Do not release the Redis lease.
            raise

        # Only remove the instance after a successful stop.
        self._instances.remove(
            key,
            expected_agent=entry.runtime,
        )

        self._active.pop(key, None)
        self._failed.discard(key)

        if entry.heartbeat is not None:
            entry.heartbeat.cancel()

        await self._presence.release(
            key,
            lease=entry.lease,
        )

        return AgentLifecycleResult(
            key=key,
            state=AgentLifecycleState.SLEEPING,
        )

    async def status(
        self,
        key: AgentInstanceKey,
    ) -> AgentLifecycleResult:
        async with self._instances.lock(key):
            await self._authorize(key, "status")
            entry = self._active.get(key)

            if entry is None:
                return AgentLifecycleResult(
                    key=key,
                    state=(
                        AgentLifecycleState.FAILED
                        if key in self._failed
                        else AgentLifecycleState.SLEEPING
                    ),
                )

            owned = await self._presence.is_owner(
                key,
                lease=entry.lease,
            )

            return AgentLifecycleResult(
                key=key,
                state=(
                    AgentLifecycleState.ACTIVE
                    if owned and key not in self._failed
                    else AgentLifecycleState.FAILED
                ),
            )

    async def _keep_alive(
        self,
        key: AgentInstanceKey,
        entry: _ActiveAgent,
    ) -> None:
        assert self._authority is not None
        try:
            while True:
                await asyncio.sleep(min(self._heartbeat_interval, self._authority.poll_interval))
                if not entry.stopping:
                    try:
                        await self._authority.check_permit(entry.permit)
                    except EventPermissionError:
                        entry.stopping = True
                        entry.runtime.revoke()
                        self._failed.add(key)
                        self._schedule_cleanup(key)

                async with asyncio.timeout(1.0):
                    renewed = await self._presence.renew(key, lease=entry.lease)

                if not renewed:
                    break

        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception(
                "Lease renewal error: %s",
                key.agent_id,
            )

        entry.runtime.revoke()
        self._failed.add(key)

        logger.error(
            "Agent lost runtime ownership: %s",
            key.agent_id,
        )

        # Schedule cleanup outside this heartbeat task,
        # so deactivation can independently manage it.
        asyncio.create_task(
            self._cleanup_lost_runtime(key),
            name=f"caos:agent:cleanup:{key.agent_id}",
        )

    async def _cleanup_lost_runtime(
        self,
        key: AgentInstanceKey,
    ) -> None:
        try:
            await self._deactivate(key)
        except Exception:
            logger.exception(
                "Failed to clean up agent %s",
                key.agent_id,
            )

    async def shutdown(self) -> None:
        """Called during application shutdown."""
        for task in tuple(self._starting.values()):
            task.cancel()
        await asyncio.gather(*tuple(self._starting.values()), return_exceptions=True)
        for key in tuple(self._active):
            await self._deactivate(key)
        await asyncio.gather(*tuple(self._cleanup_tasks), return_exceptions=True)
