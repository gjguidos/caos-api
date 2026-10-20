"""
AgentChatService: Service for handling agent chat interactions.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any
from uuid import uuid4

from src.base.config.config import settings
from src.base.entities.user import User
from src.base.handlers.exception import classify_error
from src.base.services.file_service import (
    FileMetadata,
    FileMissingError,
    FileOwnershipError,
    FileService,
)
from src.domains.agentverse.agents.base import BaseAgent
from src.domains.agentverse.agents.mixins.knowledge import KnowledgeCollectionError, KnowledgePolicy
from src.domains.agentverse.agents.utils.helpers import json_safe
from src.domains.agentverse.cache import helpers as av_cache
from src.domains.agentverse.context.connection_context import ConnCtx
from src.domains.agentverse.logging.logger_v1 import AGENTVERSE_LOGGER, LogDomain
from src.domains.agentverse.registries.registries import tool_registry_instance
from src.domains.agentverse.repositories.run_report_repository import RunReportRepository
from src.domains.agentverse.runtime.instances import AgentInstanceRegistry
from src.domains.agentverse.services.agent_service import AgentService
from src.domains.agentverse.utils.canonicalize import canonicalize

logger = logging.getLogger(__name__)

# Matches the first @<tool_key> token in a message (not inside a word/email).
_AT_TOOL_KEY_RE = re.compile(r"(?<!\w)@([A-Za-z0-9][\w-]*)")

# ---- Configuration Constants ---------------------------------------------------------------
TURN_TIMEOUT_MS = 190_000
SPAWN_TIMEOUT_MS = 10_000
INJECT_TIMEOUT_MS = 10_000
REMEMBER_TIMEOUT_MS = 3_000
SPAWN_LOCK_TIMEOUT = 5.0
SPAWN_LOCK_POLL = 0.1
SPAWN_GRACE_PERIOD = 3.0
STREAM_CHUNK_EVENT = "chat.stream"
STREAM_DONE_EVENT = "chat.done"


class _Timer:
    """Context manager for timing operations."""

    def __enter__(self):
        self._start = time.perf_counter()
        self._end: float | None = None
        return self

    def __exit__(self, *a):
        self._end = time.perf_counter()

    @property
    def dt(self) -> float:
        if not hasattr(self, "_start"):
            return 0.0
        end = self._end if self._end is not None else time.perf_counter()
        return (end - self._start) * 1000


class AgentChatService:
    """
    Service for handling agent chat interactions.

    This service consolidates:
    - Agent spawning and retrieval with distributed locking
    - Chat message processing with timeout protection
    - Knowledge collection management
    - Streaming and non-streaming response handling
    - Per-session turn serialization

    Important concurrency rule:
    - The runtime agent is session-local.
    - The websocket emitter is turn-local.
    """

    def __init__(
        self,
        agent_service: AgentService,
        instance_registry: AgentInstanceRegistry,
        file_service: FileService | None = None,
        run_report_repository: RunReportRepository | None = None,
    ):
        self.agent_service = agent_service
        self.instance_registry = instance_registry
        self.file_service = file_service
        self.run_report_repository = run_report_repository

    # ========================== PUBLIC API ==========================

    async def handle_chat(self, ctx: ConnCtx, data: dict) -> dict[str, Any]:
        await self._yield_point("handle_chat.enter")

        AGENTVERSE_LOGGER.info(
            LogDomain.COMMAND_ROOM,
            "[🤖 CHAT][💬 TURN] chat_turn_start",
        )

        user: User = ctx.websocket.scope.get("user")
        if not user:
            AGENTVERSE_LOGGER.warning(
                LogDomain.COMMAND_ROOM,
                "[🤖 CHAT][⚠️ AUTH] chat_turn_rejected",
                extra={"reason": "unauthenticated_user"},
            )
            return {"error": "User not authenticated"}

        text = self._extract_text(data)
        if not isinstance(text, str):
            text = str(text or "")
        text = text.strip()

        if not text:
            AGENTVERSE_LOGGER.warning(
                LogDomain.COMMAND_ROOM,
                "[🤖 CHAT][⚠️ INPUT] chat_turn_rejected",
                extra={"reason": "empty_message"},
            )
            return {
                "status": "error",
                "code": "EMPTY_MESSAGE",
                "message": "No message text provided.",
            }

        base_tool_kwargs = (data.get("tool_kwargs") or data.get("kwargs") or {}) or {}
        stream_requested = bool(data.get("stream") or base_tool_kwargs.get("stream"))

        turn_tool_kwargs = self._build_turn_tool_kwargs(
            ctx=ctx,
            tool_kwargs=base_tool_kwargs,
        )

        # Validate and enrich any uploaded files before spawning the agent
        raw_files = data.get("files") or []
        if raw_files:
            validated_files, file_error = await self._validate_files(
                raw_files=raw_files,
                text=text,
                user=user,
            )
            if file_error:
                return file_error
            turn_tool_kwargs = {
                **turn_tool_kwargs,
                "files": [f.model_dump() for f in validated_files],
            }

        AGENTVERSE_LOGGER.debug(
            LogDomain.COMMAND_ROOM,
            "[🤖 CHAT][🛁 INPUT] chat_turn_normalized",
            extra={
                "stream": stream_requested,
                "text_len": len(text),
                "tool_kwarg_keys": list(base_tool_kwargs.keys()),
                "turn_tool_kwarg_keys": list(turn_tool_kwargs.keys()),
                "turn_emitter_id": id(ctx.socket_emitter),
            },
        )

        if not hasattr(ctx, "_turn_sem"):
            ctx._turn_sem = asyncio.Semaphore(1)

        release_turn_gate = True

        await self._yield_point("handle_chat.before_turn_lock")
        await ctx._turn_sem.acquire()

        AGENTVERSE_LOGGER.debug(
            LogDomain.COMMAND_ROOM,
            "[🤖 CHAT][🚥 TURN] turn_gate_locked",
        )

        try:
            agent = await self._get_or_spawn_agent_with_timeout(ctx, user)
            if isinstance(agent, dict) and agent.get("status") == "error":
                return agent

            await self._yield_point("handle_chat.after_spawn")

            AGENTVERSE_LOGGER.info(
                LogDomain.COMMAND_ROOM,
                "[🤖 CHAT][🚀 SPAWN] eva_synchronized",
            )

            collection_result = await self._handle_collections(
                ctx,
                agent,
                data,
                turn_tool_kwargs,
            )

            if isinstance(collection_result, dict) and collection_result.get("status") == "error":
                return collection_result

            turn_tool_kwargs = self._build_turn_tool_kwargs(
                ctx=ctx,
                tool_kwargs=collection_result,
            )

            await self._yield_point("handle_chat.after_collections")

            if stream_requested and hasattr(agent, "respond_stream"):
                AGENTVERSE_LOGGER.info(
                    LogDomain.COMMAND_ROOM,
                    "[🤖 CHAT][📡 STREAM] stream_mode_engaged",
                )

                release_turn_gate = False
                return await self._handle_streaming_response(
                    ctx, agent, text, turn_tool_kwargs, user=user
                )

            AGENTVERSE_LOGGER.debug(
                LogDomain.COMMAND_ROOM,
                "[🤖 CHAT][📲 TURN] regular_mode_engaged",
            )

            return await self._handle_regular_response(
                ctx, agent, text, turn_tool_kwargs, user=user
            )

        finally:
            if release_turn_gate:
                try:
                    ctx._turn_sem.release()
                except Exception:
                    pass

    async def test_agent(
        self,
        socket_emitter,
        request,
        agent_id: str,
        user: User,
        socket_id: str,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            AGENTVERSE_LOGGER.info(
                LogDomain.COMMAND_ROOM,
                "[🤖 CHAT][🧪 TEST] self_test_start",
            )

            agent = await self.get_or_spawn_agent(
                socket_emitter=socket_emitter,
                request=request,
                socket_id=socket_id,
                agent_id=agent_id,
                user=user,
                tenant_id=tenant_id,
            )

            tool_kwargs = {
                "_socket_emitter": socket_emitter,
                "_session_id": socket_id,
                "_agent_id": agent_id,
            }

            async with agent.turn_lock:
                AGENTVERSE_LOGGER.info(
                    LogDomain.COMMAND_ROOM,
                    "[🤖 CHAT][💬 TURN] before_agent_respond",
                )

                response = await agent.respond(
                    "Hello. Can you hear me?",
                    session_id=socket_id,
                    tool_kwargs=tool_kwargs,
                )

                AGENTVERSE_LOGGER.info(
                    LogDomain.COMMAND_ROOM,
                    "[🤖 CHAT][💬 TURN] after_agent_respond",
                )

                response_text = self._extract_response_text(response)
                await agent.remember("first_response", response_text)

            await agent.sleep(request)

            AGENTVERSE_LOGGER.info(
                LogDomain.COMMAND_ROOM,
                "[🤖 CHAT][🧪 TEST] self_test_complete",
            )

            return {
                "status": "success",
                "response_text": response_text,
                "message": f"EVA '{agent.system_name}' successfully self-tested and deactivated.",
            }

        except Exception as e:
            AGENTVERSE_LOGGER.exception(
                LogDomain.COMMAND_ROOM,
                "[🤖 CHAT][💥 FAIL] self_test_failed",
            )

            message, code = classify_error(e)
            return {
                "status": "error",
                "code": code,
                "message": message,
            }

    async def get_or_spawn_agent(
        self,
        socket_emitter,
        request,
        socket_id: str,
        agent_id: str,
        user: User,
        tenant_id: str | None = None,
    ):
        repo = av_cache.get_cache_repo(request)
        spawn_key = av_cache.agent_spawn_key(
            request,
            user.id,
            agent_id,
            tenant_id,
            socket_id,
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.COMMAND_ROOM,
            "[🤖 CHAT][🚀 SPAWN] spawn_sync_start",
        )

        inst = self.instance_registry.get(user.id, agent_id, socket_id)
        if inst and await inst.is_spawned(request):
            inst.bind_runtime(request=request, default_session_id=socket_id, tenant_id=tenant_id)
            await av_cache.touch_spawn(
                request,
                user.id,
                agent_id,
                tenant_id,
                socket_id,
            )

            AGENTVERSE_LOGGER.debug(
                LogDomain.COMMAND_ROOM,
                "[🤖 CHAT][🚀 SPAWN] spawn_sync_registry_hit",
            )
            return inst

        if (await repo.get(spawn_key)) == "true":
            await av_cache.touch_spawn(
                request,
                user.id,
                agent_id,
                tenant_id,
                socket_id,
            )

            AGENTVERSE_LOGGER.debug(
                LogDomain.COMMAND_ROOM,
                "[🤖 CHAT][🚀 SPAWN] spawn_sync_cache_hit",
            )

            if inst:
                inst.bind_runtime(
                    request=request, default_session_id=socket_id, tenant_id=tenant_id
                )
                return inst

        try:
            async with av_cache.acquire_spawn_lock(
                request,
                user.id,
                agent_id,
                tenant_id,
                socket_id,
                wait_timeout=SPAWN_LOCK_TIMEOUT,
                poll=SPAWN_LOCK_POLL,
            ):
                if (await repo.get(spawn_key)) == "true":
                    inst = self.instance_registry.get(user.id, agent_id, socket_id)
                    if inst:
                        AGENTVERSE_LOGGER.debug(
                            LogDomain.COMMAND_ROOM,
                            "[🤖 CHAT][🚀 SPAWN] spawn_sync_lock_recheck_hit",
                        )
                        return inst

                AGENTVERSE_LOGGER.info(
                    LogDomain.COMMAND_ROOM,
                    "[🤖 CHAT][🚀 SPAWN] eva_manifestation_start",
                )

                agent = await self.agent_service.get_runtime_agent(
                    socket_emitter=socket_emitter,
                    request=request,
                    agent_id=agent_id,
                    user=user,
                    socket_id=socket_id,
                )

                if not agent:
                    raise ValueError(f"Agent '{agent_id}' could not be found for user '{user.id}'")

                AGENTVERSE_LOGGER.info(
                    LogDomain.COMMAND_ROOM,
                    "[🤖 CHAT][🚀 SPAWN] eva_manifestation_complete",
                )

                return agent

        except TimeoutError:
            AGENTVERSE_LOGGER.warning(
                LogDomain.COMMAND_ROOM,
                "[🤖 CHAT][⏱ TIMEOUT] spawn_lock_timeout",
            )

            deadline = time.monotonic() + SPAWN_GRACE_PERIOD
            while time.monotonic() < deadline:
                if (await repo.get(spawn_key)) == "true":
                    inst = self.instance_registry.get(user.id, agent_id, socket_id)
                    if inst:
                        AGENTVERSE_LOGGER.info(
                            LogDomain.COMMAND_ROOM,
                            "[🤖 CHAT][🚀 SPAWN] spawn_sync_recovered",
                        )
                        return inst

                await asyncio.sleep(0.1)

            raise

    # ========================== PRIVATE HELPERS ==========================

    @staticmethod
    def _build_turn_tool_kwargs(
        *,
        ctx: ConnCtx,
        tool_kwargs: dict | None,
    ) -> dict[str, Any]:
        """
        Builds kwargs for one chat turn.

        The socket emitter must be turn-local because the runtime agent may be shared
        across multiple browser windows.
        """
        out = dict(tool_kwargs or {})
        out["_socket_emitter"] = ctx.socket_emitter
        out["_session_id"] = ctx.session_id
        out["_agent_id"] = ctx.agent_id
        return out

    @staticmethod
    def _extract_text(payload: dict) -> str:
        if not isinstance(payload, dict):
            return ""

        m = payload.get("message")
        if isinstance(m, str):
            return m

        if isinstance(m, dict):
            return m.get("text") or m.get("content") or ""

        return payload.get("text") or payload.get("content") or ""

    @staticmethod
    def _extract_response_text(response) -> str:
        if isinstance(response, dict):
            return response.get("message") or response.get("text") or str(response)
        return str(response)

    async def _get_or_spawn_agent_with_timeout(self, ctx: ConnCtx, user: User):
        with _Timer() as tt:
            try:
                agent = await asyncio.wait_for(
                    self.get_or_spawn_agent(
                        socket_emitter=ctx.socket_emitter,
                        request=ctx.request,
                        socket_id=ctx.session_id,
                        agent_id=ctx.agent_id,
                        user=user,
                        tenant_id=getattr(ctx, "tenant_id", None),
                    ),
                    timeout=SPAWN_TIMEOUT_MS / 1000,
                )

            except TimeoutError:
                AGENTVERSE_LOGGER.warning(
                    LogDomain.COMMAND_ROOM,
                    "[🤖 CHAT][⏱ TIMEOUT] spawn_timeout",
                )
                return {
                    "status": "error",
                    "code": "SPAWN_TIMEOUT",
                    "message": "Agent spawn timed out.",
                }

        AGENTVERSE_LOGGER.debug(
            LogDomain.COMMAND_ROOM,
            "[🤖 CHAT][🚀 SPAWN] spawn_sync_complete",
            extra={"duration_ms": round(tt.dt, 2)},
        )

        return agent

    async def _handle_collections(
        self,
        ctx: ConnCtx,
        agent,
        data: dict,
        tool_kwargs: dict,
    ) -> dict[str, Any]:
        policy = KnowledgePolicy(mode="single", allow_public_fallback=False)

        def _norm(s):
            return (s or "").strip()

        incoming_active = _norm(
            data.get("active_collection")
            or tool_kwargs.get("active_collection")
            or tool_kwargs.get("collection")
        )

        agent_collections = list(getattr(agent, "collections", []) or [])
        if not incoming_active and len(agent_collections) == 1:
            incoming_active = agent_collections[0]

        if incoming_active:
            if agent_collections and incoming_active not in agent_collections:
                AGENTVERSE_LOGGER.warning(
                    LogDomain.COMMAND_ROOM,
                    "[🤖 CHAT][📚 RAG] collection_rejected",
                    extra={
                        "requested_collection": incoming_active,
                        "allowed_collections": agent_collections,
                    },
                )
                return {
                    "status": "error",
                    "error": {
                        "code": "invalid_collection",
                        "message": (
                            f"active_collection '{incoming_active}' is not linked to this agent. "
                            f"Allowed: {agent_collections}"
                        ),
                    },
                    "session_id": ctx.session_id,
                }

            try:
                with _Timer() as tt:
                    await asyncio.wait_for(
                        agent.remember(
                            "active_collection",
                            incoming_active,
                        ),
                        timeout=REMEMBER_TIMEOUT_MS / 1000,
                    )

                AGENTVERSE_LOGGER.debug(
                    LogDomain.COMMAND_ROOM,
                    "[🤖 CHAT][🧠 MEMORY] collection_memory_updated",
                    extra={
                        "collection": incoming_active,
                        "duration_ms": round(tt.dt, 2),
                    },
                )

            except TimeoutError:
                AGENTVERSE_LOGGER.warning(
                    LogDomain.COMMAND_ROOM,
                    "[🤖 CHAT][⏱ TIMEOUT] collection_memory_timeout",
                    extra={
                        "collection": incoming_active,
                        "timeout_ms": REMEMBER_TIMEOUT_MS,
                    },
                )

            except Exception:
                AGENTVERSE_LOGGER.exception(
                    LogDomain.COMMAND_ROOM,
                    "[🤖 CHAT][💥 FAIL] collection_memory_failed",
                    extra={"collection": incoming_active},
                )

            tool_kwargs.setdefault("collection", incoming_active)

        try:
            with _Timer() as tt:
                tool_kwargs = await asyncio.wait_for(
                    agent.inject_collection_into_kwargs(
                        tool_kwargs=tool_kwargs,
                        incoming=incoming_active,
                        policy=policy,
                        kwarg_name="collection",
                    ),
                    timeout=INJECT_TIMEOUT_MS / 1000,
                )

            if incoming_active:
                AGENTVERSE_LOGGER.info(
                    LogDomain.COMMAND_ROOM,
                    "[🤖 CHAT][📚 RAG] collection_linked",
                    extra={
                        "collection": incoming_active,
                        "duration_ms": round(tt.dt, 2),
                    },
                )
            else:
                AGENTVERSE_LOGGER.debug(
                    LogDomain.COMMAND_ROOM,
                    "[🤖 CHAT][📚 RAG] collection_resolution_complete",
                    extra={"duration_ms": round(tt.dt, 2)},
                )

        except TimeoutError:
            AGENTVERSE_LOGGER.warning(
                LogDomain.COMMAND_ROOM,
                "[🤖 CHAT][⏱ TIMEOUT] collection_injection_timeout",
                extra={"timeout_ms": INJECT_TIMEOUT_MS},
            )
            return {
                "status": "error",
                "code": "INJECT_TIMEOUT",
                "message": "Collection setup timed out.",
            }

        except KnowledgeCollectionError as e:
            AGENTVERSE_LOGGER.warning(
                LogDomain.COMMAND_ROOM,
                "[🤖 CHAT][📚 RAG] collection_missing",
                extra={"error": str(e)},
            )
            return {
                "status": "error",
                "code": "NO_ACTIVE_COLLECTION",
                "message": str(e),
            }

        return self._build_turn_tool_kwargs(ctx=ctx, tool_kwargs=tool_kwargs)

    async def _handle_streaming_response(
        self,
        ctx: ConnCtx,
        agent,
        text: str,
        tool_kwargs: dict,
        user: User | None = None,
    ) -> dict[str, Any]:
        stream_id = uuid4().hex
        _stream_file_ids = [
            f.get("file_id")
            for f in (tool_kwargs.get("files") or [])
            if isinstance(f, dict) and f.get("file_id")
        ]
        _stream_user_id = (user.id if user else None) or ctx.user_id

        async def _run_stream():
            try:
                async with asyncio.timeout(TURN_TIMEOUT_MS / 1000):
                    AGENTVERSE_LOGGER.info(
                        LogDomain.COMMAND_ROOM,
                        "[🤖 CHAT][📡 STREAM] stream_start",
                        extra={
                            "stream_id": stream_id,
                            "session_id": ctx.session_id,
                            "emitter_id": id(ctx.socket_emitter),
                        },
                    )

                    async for chunk in agent.respond_stream(
                        text,
                        session_id=ctx.session_id,
                        tool_kwargs=tool_kwargs,
                    ):
                        try:
                            await ctx.socket_emitter.event(
                                STREAM_CHUNK_EVENT,
                                {"id": stream_id, "delta": chunk},
                            )
                        except Exception:
                            AGENTVERSE_LOGGER.exception(
                                LogDomain.COMMAND_ROOM,
                                "[🤖 CHAT][💥 FAIL] stream_chunk_emit_failed",
                                extra={"stream_id": stream_id},
                            )
                            break

            except TimeoutError:
                AGENTVERSE_LOGGER.warning(
                    LogDomain.COMMAND_ROOM,
                    "[🤖 CHAT][⏱ TIMEOUT] stream_timeout",
                    extra={
                        "stream_id": stream_id,
                        "timeout_ms": TURN_TIMEOUT_MS,
                    },
                )
                await ctx.socket_emitter.event(
                    STREAM_DONE_EVENT,
                    {
                        "id": stream_id,
                        "error": "TURN_TIMEOUT",
                    },
                )

            except Exception:
                AGENTVERSE_LOGGER.exception(
                    LogDomain.COMMAND_ROOM,
                    "[🤖 CHAT][💥 FAIL] stream_failed",
                    extra={"stream_id": stream_id},
                )

            finally:
                try:
                    await ctx.socket_emitter.event(
                        STREAM_DONE_EVENT,
                        {"id": stream_id},
                    )
                except Exception:
                    pass

                AGENTVERSE_LOGGER.info(
                    LogDomain.COMMAND_ROOM,
                    "[🤖 CHAT][📡 STREAM] stream_complete",
                    extra={"stream_id": stream_id},
                )

                try:
                    ctx._turn_sem.release()
                except Exception:
                    pass

                if _stream_file_ids and self.file_service and _stream_user_id:
                    try:
                        await self.file_service.delete_many(
                            _stream_file_ids, user_id=_stream_user_id
                        )
                    except Exception:
                        logger.exception(
                            "Stream per-turn file cleanup failed for file_ids=%s",
                            _stream_file_ids,
                        )

        asyncio.create_task(_run_stream())

        return {
            "status": "started",
            "stream_id": stream_id,
            "session_id": ctx.session_id,
        }

    async def _handle_regular_response(
        self,
        ctx: ConnCtx,
        agent: BaseAgent,
        text: str,
        tool_kwargs: dict,
        user: User | None = None,
    ) -> dict[str, Any]:
        file_ids = [
            f.get("file_id")
            for f in (tool_kwargs.get("files") or [])
            if isinstance(f, dict) and f.get("file_id")
        ]
        user_id = (user.id if user else None) or ctx.user_id

        with _Timer() as tt:
            try:
                respond_started = time.perf_counter()

                AGENTVERSE_LOGGER.info(
                    LogDomain.COMMAND_ROOM,
                    "[🤖 CHAT][💬 TURN] agent_respond_start",
                    extra={
                        "agent_id": ctx.agent_id,
                        "session_id": ctx.session_id,
                        "emitter_id": id(ctx.socket_emitter),
                    },
                )

                reply = await asyncio.wait_for(
                    agent.respond(
                        text,
                        session_id=ctx.session_id,
                        tool_kwargs=tool_kwargs,
                    ),
                    timeout=TURN_TIMEOUT_MS / 1000,
                )

                AGENTVERSE_LOGGER.info(
                    LogDomain.COMMAND_ROOM,
                    "[🤖 CHAT][💬 TURN] agent_respond_complete",
                    extra={
                        "agent_id": ctx.agent_id,
                        "session_id": ctx.session_id,
                        "emitter_id": id(ctx.socket_emitter),
                        "duration_ms": round(
                            (time.perf_counter() - respond_started) * 1000,
                            2,
                        ),
                    },
                )

            except TimeoutError:
                AGENTVERSE_LOGGER.warning(
                    LogDomain.COMMAND_ROOM,
                    "[🤖 CHAT][⏱ TIMEOUT] turn_timeout",
                    extra={"timeout_ms": TURN_TIMEOUT_MS},
                )
                return {
                    "status": "error",
                    "code": "TURN_TIMEOUT",
                    "message": "Chat turn timed out.",
                    "session_id": ctx.session_id,
                }
            finally:
                if file_ids and self.file_service and user_id:
                    try:
                        await self.file_service.delete_many(file_ids, user_id=user_id)
                    except Exception:
                        logger.exception("Per-turn file cleanup failed for file_ids=%s", file_ids)

            await self._yield_point("handle_chat.after_respond")

            if isinstance(reply, str):
                reply = {"kind": "text", "text": reply}

            if not isinstance(reply, dict):
                reply = {"kind": "text", "text": str(reply)}

            safe_reply = json_safe(reply)

            # The frontend reads report/data from the envelope top level, so
            # strip them from the nested reply to avoid sending each large
            # object twice. (json_safe returns a fresh dict, so popping here
            # does not mutate the agent's stored reply.) The auditor's findings
            # now live in report.tool_reports rather than a sibling key.
            top_report = safe_reply.pop("report", None)
            reply_data = safe_reply.pop("data", None)

            await self._persist_run_report(
                report=top_report,
                agent_id=ctx.agent_id,
                session_id=ctx.session_id,
                created_by=user_id,
            )

            AGENTVERSE_LOGGER.info(
                LogDomain.COMMAND_ROOM,
                "[🤖 CHAT][🏁 TURN] chat_turn_complete",
                extra={
                    "reply_kind": safe_reply.get("kind"),
                    "duration_ms": round(tt.dt, 2),
                },
            )

            return {
                "status": "ok",
                "message": safe_reply,
                "report": top_report,
                "data": reply_data,
                "session_id": ctx.session_id,
            }

    async def _persist_run_report(
        self,
        *,
        report: Any,
        agent_id: str | None,
        session_id: str | None,
        created_by: str | None,
    ) -> None:
        """Store the turn's run report, stamping the requesting user as owner.

        This service is the lowest layer that knows who is asking - the
        orchestrator deliberately does not - so ownership is applied here and
        the read API filters on it. A failure to persist is logged and dropped:
        losing a diagnostic record must never cost the user their answer.
        """
        if not self.run_report_repository:
            return
        if not settings.run_report.run_report_persistence_enabled:
            return

        run_id = (
            ((report or {}).get("run") or {}).get("run_id") if isinstance(report, dict) else None
        )
        if not run_id:
            return

        # The read API scopes every query by owner, so an unowned document
        # would be written and then be unreachable forever. Say so rather than
        # storing a report nobody can ever fetch.
        if not created_by:
            AGENTVERSE_LOGGER.warning(
                LogDomain.COMMAND_ROOM,
                "[🤖 CHAT][⚠️ REPORT] run_report_skipped_no_owner",
                extra={"run_id": run_id, "session_id": session_id, "agent_id": agent_id},
            )
            return

        try:
            await self.run_report_repository.store(
                run_id=run_id,
                report=report,
                session_id=session_id,
                agent_id=agent_id,
                created_by=created_by,
            )
        except Exception:
            AGENTVERSE_LOGGER.exception(
                LogDomain.COMMAND_ROOM,
                "[🤖 CHAT][💥 FAIL] run_report_persist_failed",
                extra={"run_id": run_id, "session_id": session_id},
            )

    # ========================== FILE VALIDATION ==========================

    @staticmethod
    def _resolve_tool_key(text: str) -> tuple[str, dict] | None:
        """
        Resolve the tool targeted by an ``@<tool_key>`` token in the message.

        Selection is canonical-key-only: the token after '@' is canonicalized and must
        match a registered tool key. Returns (canonical_key, registry_meta) for the
        first match, or None if no @tool_key matches a registered tool.
        """
        if not text:
            return None
        for m in _AT_TOOL_KEY_RE.finditer(text):
            token = canonicalize(m.group(1))
            if token in tool_registry_instance:
                return token, tool_registry_instance.get_metadata_for_ref(token)
        return None

    @staticmethod
    def _file_validation_error(code: str, message: str) -> dict:
        return {
            "status": "error",
            "code": code,
            "message": message,
        }

    @staticmethod
    def _files_support_from_meta(meta: dict):
        supports = meta.get("supports") or {}
        files_config_raw = supports.get("files")
        if not files_config_raw:
            return None

        from src.domains.agentverse.tools.spec import FilesSupport

        return (
            FilesSupport(**files_config_raw)
            if isinstance(files_config_raw, dict)
            else FilesSupport()
        )

    async def _validate_file_entry(
        self,
        *,
        entry: dict,
        user: User,
        resolved_key: str,
    ) -> tuple[FileMetadata | None, dict | None]:
        file_id = entry.get("file_id") if isinstance(entry, dict) else None
        if not file_id:
            return None, self._file_validation_error(
                "INVALID_FILE_ENTRY",
                "Each file entry must include a file_id.",
            )

        try:
            file_meta = await self.file_service.get_metadata(
                file_id,
                requesting_user_id=user.id or "",
            )
        except FileOwnershipError:
            return None, self._file_validation_error(
                "FILE_NOT_OWNED",
                f"File '{file_id}' does not belong to this user.",
            )
        except FileMissingError:
            return None, self._file_validation_error(
                "FILE_NOT_FOUND",
                f"File '{file_id}' was not found.",
            )

        if file_meta.target_tool != resolved_key:
            return None, self._file_validation_error(
                "FILE_TARGET_MISMATCH",
                (
                    f"File '{file_id}' was uploaded for tool '{file_meta.target_tool}', "
                    f"but the message targets '{resolved_key}'."
                ),
            )

        return file_meta, None

    async def _validate_files(
        self,
        raw_files: list[dict],
        text: str,
        user: User,
    ) -> tuple[list[FileMetadata], dict | None]:
        """
        Validate uploaded file references from a WebSocket message.

        Returns (validated_files, None) on success, or ([], error_dict) on failure.
        Error dicts follow the {status, code, message} envelope.
        """
        if not raw_files:
            return [], None

        tool_key_result = self._resolve_tool_key(text)
        if tool_key_result is None:
            return [], self._file_validation_error(
                "NO_TARGET_TOOL",
                "Message contains files but no recognized @tool_key was found.",
            )

        resolved_key, meta = tool_key_result
        files_config = self._files_support_from_meta(meta)

        if not files_config:
            return [], self._file_validation_error(
                "TOOL_DOES_NOT_SUPPORT_FILES",
                f"Tool '{resolved_key}' does not support file uploads.",
            )

        if len(raw_files) > files_config.max_count:
            return [], self._file_validation_error(
                "TOO_MANY_FILES",
                (
                    f"Too many files: received {len(raw_files)}, "
                    f"max allowed {files_config.max_count}."
                ),
            )

        if self.file_service is None:
            return [], self._file_validation_error(
                "FILE_STORAGE_UNAVAILABLE",
                "File storage is not configured on this server.",
            )

        validated: list[FileMetadata] = []
        for entry in raw_files:
            file_meta, error = await self._validate_file_entry(
                entry=entry,
                user=user,
                resolved_key=resolved_key,
            )
            if error:
                return [], error
            if file_meta is None:
                continue
            validated.append(file_meta)

        return validated, None

    @staticmethod
    async def _yield_point(label: str):
        start = time.perf_counter()
        await asyncio.sleep(0)

        dt = (time.perf_counter() - start) * 1000
        if dt > 200:
            AGENTVERSE_LOGGER.warning(
                LogDomain.COMMAND_ROOM,
                "[🤖 CHAT][⚠️ LOOP] event_loop_stall",
                extra={
                    "label": label,
                    "stall_ms": round(dt, 2),
                },
            )
