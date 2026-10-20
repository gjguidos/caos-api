from __future__ import annotations

from typing import Any

from src.domains.agentverse.agents.base import BaseAgent
from src.domains.agentverse.agents.orchestrator.quality_phases import apply_quality_gate
from src.domains.agentverse.agents.orchestrator.report import build_run_report, error_run_report
from src.domains.agentverse.agents.specs.chat_agent import chat_spec
from src.domains.agentverse.agents.utils.helpers import (
    assistant_history_text_from_reply as _assistant_history_text_from_reply,
)
from src.domains.agentverse.agents.utils.helpers import json_safe
from src.domains.agentverse.agents.utils.helpers import normalize_reply as _normalize_reply
from src.domains.agentverse.agents.utils.helpers import parse_flags as _parse_flags
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.registries.registries import (
    agent_registry_instance,
    role_registry_instance,
)


@agent_registry_instance.bind_spec(chat_spec)
class ChatAgent(BaseAgent):
    async def respond(
        self,
        user_input: str,
        file: Any = None,
        session_id: str | None = None,
        tool_kwargs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._log_respond_start(user_input=user_input, file=file, session_id=session_id)

        sid = session_id
        clean_user_input, user_flags = self._parse_user_input(user_input)

        if not self._can_use_orchestrator():
            AGENTVERSE_LOGGER.warning(
                LogDomain.EVA_UNIT,
                "[🤖 CHAT][⚠️ FALLBACK] orchestrator_unavailable",
                extra={
                    "agent_name": self.name,
                    "role_name": self.role_name,
                },
            )
            return await self._fallback_respond(
                user_input=user_input,
                file=file,
                session_id=session_id,
                tool_kwargs=tool_kwargs,
            )

        spec = self._resolve_role_spec()
        system_flags = self._build_system_flags(spec)

        history = await self._fetch_history(session_id=sid)

        ctx = await self._execute_orchestrator(
            spec=spec,
            clean_user_input=clean_user_input,
            raw_user_input=user_input,
            session_id=sid,
            history=history,
            user_flags=user_flags,
            system_flags=system_flags,
            tool_kwargs=tool_kwargs,
        )

        self._log_orchestrator_completed(session_id=sid)

        await self._store_user_turn(
            user_input=user_input,
            session_id=sid,
            rendered_user=ctx.get("rendered_user"),
        )

        reply = self._build_reply(ctx)
        await self._store_assistant_turn(reply=reply, session_id=sid)

        self._store_debug_handles(ctx=ctx, reply=reply, session_id=sid)
        return reply

    async def _fallback_respond(
        self,
        user_input: str,
        file: Any = None,
        session_id: str | None = None,
        tool_kwargs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            return await super().respond(
                user_input,
                file=file,
                session_id=session_id,
                tool_kwargs=tool_kwargs,
            )  # type: ignore[misc]
        except Exception:
            AGENTVERSE_LOGGER.warning(
                LogDomain.EVA_UNIT,
                "[🤖 CHAT][⚠️ FALLBACK] fallback_echo_return",
                extra={"agent_name": self.name},
            )
            return {"kind": "text", "text": str(user_input or "")}

    async def _fetch_history(self, session_id: str | None) -> list[dict[str, Any]]:
        try:
            AGENTVERSE_LOGGER.debug(
                LogDomain.EVA_UNIT,
                "[🤖 CHAT][🧲 HISTORY] history_fetch_start",
                extra={"agent_name": self.name},
            )
            return await self.get_memory_history(limit=50, session_id=session_id) or []
        except Exception:
            AGENTVERSE_LOGGER.warning(
                LogDomain.EVA_UNIT,
                "[🤖 CHAT][⚠️ HISTORY] history_fetch_failed",
                extra={"agent_name": self.name},
            )
            return []

    async def _execute_orchestrator(
        self,
        spec: Any,
        clean_user_input: str,
        raw_user_input: str,
        session_id: str | None,
        history: list[dict[str, Any]],
        user_flags: dict[str, Any],
        system_flags: dict[str, Any],
        tool_kwargs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        tool_kwargs = dict(tool_kwargs or {})

        AGENTVERSE_LOGGER.info(
            LogDomain.EVA_UNIT,
            "[🤖 CHAT][🧭 ORCH] orchestrator_execute_start",
            extra={
                "agent_name": self.name,
                "history_len": len(history),
                "session_id": session_id,
                "has_turn_emitter": "_socket_emitter" in tool_kwargs,
                "turn_emitter_id": id(tool_kwargs.get("_socket_emitter"))
                if tool_kwargs.get("_socket_emitter")
                else None,
            },
        )

        extra = {
            "agent": self,
            "tool_manager": getattr(self, "tool_manager", None),
            "trace_id": session_id,
            "policy": spec.policy,
            "tool_kwargs": tool_kwargs,
            "_socket_emitter": tool_kwargs.get("_socket_emitter"),
            "_session_id": session_id,
            "_agent_id": tool_kwargs.get("_agent_id") or self.id,
        }

        return await self.orchestrator.plan_and_execute(
            handlers=apply_quality_gate(spec.handlers, extra),
            ctx={
                "role": self.role_name,
                "request": clean_user_input,
                "raw_request": raw_user_input,
                "session_id": session_id,
                "goal": clean_user_input,
                "history": history,
                "flags": {
                    "user": user_flags,
                    "system": system_flags,
                },
                "tool_kwargs": tool_kwargs,
                "_socket_emitter": tool_kwargs.get("_socket_emitter"),
                "_agent_id": tool_kwargs.get("_agent_id") or self.id,
            },
            extra=extra,
        )

    async def _store_user_turn(
        self,
        user_input: str,
        session_id: str | None,
        rendered_user: str | None = None,
    ) -> None:
        try:
            # For context_based tool turns (local or proxy/external), persist the
            # constructed prompt as the model-facing content and keep the raw @tool
            # command in meta (audit only, never sent to the model). The invoker
            # extracts the prompt text (incl. multimodal overrides) before it reaches
            # here. Plain-chat / pure-tool turns store the raw input with no meta.
            if rendered_user:
                await self.append_entry(
                    "user",
                    rendered_user,
                    session_id=session_id,
                    meta={"raw_input": user_input},
                )
            else:
                await self.append_entry("user", user_input, session_id=session_id)
        except Exception:
            AGENTVERSE_LOGGER.warning(
                LogDomain.EVA_UNIT,
                "[🤖 CHAT][⚠️ MEMORY] user_turn_store_failed",
                extra={"agent_name": self.name},
            )

    async def _store_assistant_turn(self, reply: dict[str, Any], session_id: str | None) -> None:
        try:
            await self.append_entry(
                "assistant",
                _assistant_history_text_from_reply(reply),
                session_id=session_id,
            )
        except Exception:
            AGENTVERSE_LOGGER.warning(
                LogDomain.EVA_UNIT,
                "[🤖 CHAT][⚠️ MEMORY] assistant_turn_store_failed",
                extra={"agent_name": self.name},
            )

    def _store_debug_handles(
        self,
        ctx: dict[str, Any] | None,
        reply: dict[str, Any],
        session_id: str | None = None,
    ) -> None:
        try:
            if not hasattr(self, "_last_by_session"):
                self._last_by_session = {}

            key = session_id or "default"
            self._last_by_session[key] = {
                "ctx": ctx,
                "report": reply.get("report"),
            }

            self._last_ctx = ctx
            self._last_report = reply.get("report")
        except Exception:
            pass

    def _build_reply(self, ctx: dict[str, Any] | None) -> dict[str, Any]:
        artifact = (ctx or {}).get("artifact") or {}

        reply = _normalize_reply(artifact)
        reply["report"] = self._build_safe_report(ctx)

        AGENTVERSE_LOGGER.debug(
            LogDomain.EVA_UNIT,
            "[🤖 CHAT][🏗️ REPLY] reply_built",
            extra={
                "agent_name": self.name,
                "reply_kind": reply.get("kind"),
            },
        )

        return reply

    def _build_safe_report(self, ctx: dict[str, Any] | None) -> dict[str, Any]:
        """Always hand back a report object.

        ``build_run_report`` already degrades field-by-field, so the only thing
        left to guard is a failure in the builder itself. Callers get the v2
        shape either way - never the empty string v1 fell back to, which broke
        every consumer expecting an object.
        """
        AGENTVERSE_LOGGER.debug(
            LogDomain.EVA_UNIT,
            "[🤖 CHAT][🏗️ REPORT] report_build_start",
            extra={"agent_name": self.name},
        )

        try:
            report = build_run_report(ctx)
        except Exception as e:
            AGENTVERSE_LOGGER.exception(
                LogDomain.EVA_UNIT,
                "[🤖 CHAT][💥 FAIL] report_build_failed",
                extra={"agent_name": self.name},
            )
            report = error_run_report(f"{type(e).__name__}: {e}")

        return json_safe(report)

    def _log_respond_start(self, user_input: str, file: Any, session_id: str | None) -> None:
        AGENTVERSE_LOGGER.info(
            LogDomain.EVA_UNIT,
            "[🤖 CHAT][💬 TURN] respond_start",
            extra={
                "agent_name": self.name,
                "has_file": file is not None,
                "input_len": len(str(user_input or "")),
            },
        )

    def _parse_user_input(self, user_input: str) -> tuple[str, dict[str, Any]]:
        return _parse_flags(user_input or "")

    def _can_use_orchestrator(self) -> bool:
        return bool(self.role_name and self.orchestrator)

    def _resolve_role_spec(self) -> Any:
        AGENTVERSE_LOGGER.debug(
            LogDomain.EVA_UNIT,
            "[🤖 CHAT][🧠 ROLE] role_resolve_start",
            extra={
                "agent_name": self.name,
                "role_name": self.role_name,
            },
        )
        return role_registry_instance.get_role(self.role_name)

    def _build_system_flags(self, spec: Any) -> dict[str, Any]:
        extras = getattr(spec.policy, "extras", {}) or {}
        return {
            "rapport": bool(extras.get("rapport", False)),
        }

    def _log_orchestrator_completed(self, session_id: str | None) -> None:
        AGENTVERSE_LOGGER.info(
            LogDomain.EVA_UNIT,
            "[🤖 CHAT][🧭 ORCH] orchestrator_execute_complete",
            extra={"agent_name": self.name},
        )
