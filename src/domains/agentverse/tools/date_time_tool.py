from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from src.base.handlers.exception import classify_error
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.registries import tool_registry_instance as tool_registry
from src.domains.agentverse.tools.base import BaseTool, ToolConfig

# --------------------------- helpers ----------------------------


def _usage_addons(start: datetime, end: datetime) -> dict[str, Any]:
    return {
        "usage": {
            "latency_ms": int((end - start).total_seconds() * 1000),
            "model": None,
            "provider": "tools:date",
            "tokens": {"input": 0, "output": 0, "total": 0},
            "context_tokens": 0,
            "estimated": True,
            "token_breakdown": {"prompt_total_est": 0},
        }
    }


# --------------------------- config -----------------------------


class DateToolConfig(ToolConfig):
    default_timezone: str = "Europe/Stockholm"
    default_format: str = "%Y-%m-%d"


# ---------------------------- tool ------------------------------


@tool_registry.register_tool(
    key="datetime",
    name="Date Tool",
    description="LLM-free date utility: returns today's date (timezone-aware). Explicit activation via @datetime.",
    version="1.0.0",
    metadata={
        "tags": ["builtin", "system"],
        "provider": "tools:date",
        "execution_mode": "pure",
        "route": "pure",
        "llm_free": True,
        "config": DateToolConfig().model_dump(),
        "outputs": [
            {"key": "answer", "label": "Date/time", "type": "string", "path": "answer"},
            {"key": "timezone", "label": "Timezone", "type": "string", "path": "timezone"},
        ],
        "ui": {
            "icon": "📅",
            "category": "utilities",
            "shortcuts": [
                {"label": "Today (local)", "overrides": {}},
                {"label": "Today (UTC)", "overrides": {"timezone": "UTC"}},
            ],
        },
    },
)
class DateTool(BaseTool):
    name = "Date Tool"
    key = "datetime"
    version = "1.0.0"
    capabilities = ("date",)
    required_dependencies: list[str] = []

    parameters: dict[str, Any] = {
        "timezone": {
            "type": "string",
            "description": "IANA timezone for date (default config.default_timezone)",
            "required": False,
        },
        "format": {
            "type": "string",
            "description": "strftime format (default config.default_format)",
            "required": False,
        },
        "text": {
            "type": "string",
            "description": "Raw user utterance (optional)",
            "required": False,
        },
        "context": {
            "type": "object",
            "description": "Tool context (may contain text/history)",
            "required": False,
        },
    }

    async def _run(
        self,
        socket_emitter,
        timezone: str | None = None,
        format: str | None = None,
        text: str | None = None,
        context: dict[str, Any] | None = None,
        **kwargs,
    ):
        started = datetime.now(UTC)

        requested_timezone = timezone
        configured_timezone = getattr(self.config, "default_timezone", "UTC")
        requested_format = format
        configured_format = getattr(self.config, "default_format", "%Y-%m-%d")

        AGENTVERSE_LOGGER.info(
            LogDomain.EVANGELION_BAY,
            "[🛠️ TOOL][🗓️ DATETIME] execution_start",
            extra={
                "tool_key": self.key,
                "tool_name": self.name,
                "version": self.version,
                "requested_timezone": requested_timezone,
                "configured_timezone": configured_timezone,
                "requested_format": requested_format,
                "configured_format": configured_format,
                "has_text": bool(text),
                "has_context": bool(context),
            },
        )

        tz_name = requested_timezone or configured_timezone
        tz_fallback_used = False

        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz_fallback_used = True
            fallback_tz_name = configured_timezone
            tz = ZoneInfo(fallback_tz_name)

            AGENTVERSE_LOGGER.warning(
                LogDomain.EVANGELION_BAY,
                "[🛠️ TOOL][🗓️ DATETIME] invalid_timezone_fallback",
                extra={
                    "requested_timezone": tz_name,
                    "fallback_timezone": fallback_tz_name,
                },
            )

        fmt = requested_format or configured_format

        try:
            now_local = datetime.now(tz)
            ended = datetime.now(UTC)

            AGENTVERSE_LOGGER.info(
                LogDomain.EVANGELION_BAY,
                "[✅ TOOL][🗓️ DATETIME] execution_success",
                extra={
                    "tool_key": self.key,
                    "timezone": tz.key,
                    "format": fmt,
                    "iso_local": now_local.isoformat(),
                    "date": now_local.strftime(fmt),
                    "latency_ms": int((ended - started).total_seconds() * 1000),
                    "tz_fallback_used": tz_fallback_used,
                },
            )

            return {
                "answer": now_local.strftime(fmt),
                "date": now_local.strftime(fmt),
                "iso_local": now_local.isoformat(),
                "timezone": tz.key,
                "addons": _usage_addons(started, ended),
            }

        except Exception as e:
            ended = datetime.now(UTC)

            AGENTVERSE_LOGGER.error(
                LogDomain.EVANGELION_BAY,
                "[💥 TOOL][🗓️ DATETIME] execution_failed",
                extra={
                    "tool_key": self.key,
                    "requested_timezone": requested_timezone,
                    "resolved_timezone": getattr(tz, "key", None),
                    "format": fmt,
                    "error_type": type(e).__name__,
                    "error": str(e),
                    "latency_ms": int((ended - started).total_seconds() * 1000),
                },
                exc_info=True,
            )

            message, code = classify_error(e)
            return self._final_error(
                message=message,
                code=code,
                result_overrides={"addons": _usage_addons(started, ended)},
            )
