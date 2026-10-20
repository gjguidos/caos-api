from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.registries import tool_registry_instance as tool_registry
from src.domains.agentverse.tools._helpers.trigger_cli_parser import (
    CLI_FLAG_RE_DEFAULT,
    extract_cli_any,
)
from src.domains.agentverse.tools.base import BaseTool
from src.domains.agentverse.tools.ideation_tool.config import IdeationToolConfig
from src.domains.agentverse.tools.ideation_tool.constants import (
    BOOL_KEYS,
    INT_KEYS,
    LIST_KEYS,
    PATTERN_IDEATE,
    SHORT_TO_LONG,
    TOPIC_KEY,
)
from src.domains.agentverse.tools.ideation_tool.flags import parse_flags
from src.domains.agentverse.tools.ideation_tool.formatter import format_ideation_result
from src.domains.agentverse.tools.ideation_tool.messages import assemble_context_bundle
from src.domains.agentverse.tools.ideation_tool.spec import ideation_tool
from src.domains.agentverse.tools.ideation_tool.sys_message import compose_system_message


@tool_registry.bind_spec(ideation_tool)
class IdeationTool(BaseTool):
    """
    Triggered ideation tool that returns ChatTool-compatible text results:
    { "result": { "answer": "<text>", "addons": { "usage": {...} } } }
    """

    name = "Ideation Tool"
    key = "ideation"
    description = "Generates idea clusters, hooks, CTAs, hashtags, and social packs as plain text."
    version = "1.4.0"
    tool_class = "ideation"
    required_dependencies: list[str] = []
    capabilities = ("campaign_ideation", "social_pack", "cta_bank", "hashtag_gen")

    parameters: dict[str, Any] = {
        "topic": {
            "type": "string",
            "required": True,
            "description": "Product/theme/campaign focus",
        },
        "mode": {
            "type": "string",
            "required": False,
            "enum": ["cluster", "social", "framework", "cta-only", "tags-only"],
        },
        "goal": {"type": "string", "required": False},
        "audience": {"type": "string", "required": False},
        "tone": {"type": "string", "required": False},
        "num_ideas": {"type": "integer", "required": False},
        "platforms": {"type": "array", "required": False, "items": {"type": "string"}},
        "brand": {"type": "string", "required": False},
        "mentions": {"type": "array", "required": False, "items": {"type": "string"}},
        "banned_words": {"type": "array", "required": False, "items": {"type": "string"}},
        "region": {"type": "string", "required": False},
        "creativity": {"type": "string", "required": False},
        "seed": {"type": "integer", "required": False},
        "frameworks": {"type": "array", "required": False, "items": {"type": "string"}},
        "constraints": {"type": "array", "required": False, "items": {"type": "string"}},
        "session_id": {"type": "string", "required": False},
        "history": {"type": "array", "required": False},
        "policy": {"type": "object", "required": False},
        "request": {"type": "object", "required": False},
    }

    _last_topic: str | None = None
    _last_num_ideas: int = 12
    _last_mode: str = "cluster"
    _last_platforms: list[str] = []
    _last_frameworks: list[str] = []
    _last_max_tags_per_platform: int | None = None
    _last_seed: int | None = None
    _last_brand: str | None = None

    def __init__(self, config: IdeationToolConfig | dict | None = None):
        cfg = self._normalize_config(config)

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🛠️ TOOL][💡 IDEATION] init",
            extra={
                "tool_key": self.key,
                "tool_name": self.name,
                "version": self.version,
                "config_type": type(cfg).__name__,
            },
        )

        super().__init__(config=cfg)

    def _normalize_config(self, config: Any) -> IdeationToolConfig:
        if isinstance(config, IdeationToolConfig):
            return config
        if isinstance(config, BaseModel):
            return IdeationToolConfig.model_validate(config)
        if isinstance(config, Mapping):
            return IdeationToolConfig(**dict(config))
        if isinstance(config, SimpleNamespace):
            return IdeationToolConfig(**vars(config))
        if isinstance(config, dict) or config is None:
            return IdeationToolConfig(**(config or {}))
        raise TypeError(f"IdeationTool config must be dict/BaseModel/namespace; got {type(config)}")

    async def provide_context(
        self,
        *,
        text: str = "",
        session_id: str | None = None,
        history: list[dict[str, str]] | None = None,
        policy: dict[str, Any] | None = None,
        request: Any | None = None,
        cli_flags: dict | None = None,
        args: dict | None = None,
        **kwargs,
    ) -> dict:
        cfg: IdeationToolConfig = self.config  # type: ignore

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🛠️ TOOL][💡 IDEATION] provide_context_start",
            extra={
                "tool_key": self.key,
                "has_text": bool(text),
                "text_len": len(text or ""),
                "has_history": bool(history),
                "history_count": len(history or []),
                "has_policy": bool(policy),
                "has_request": request is not None,
                "cli_flag_keys": list((cli_flags or {}).keys()),
            },
        )

        flags = dict(cli_flags or {})
        flags_source = "cli_flags"

        if not flags:
            flags_source = "text_parse"
            flags = extract_cli_any(
                request=(request or {}) if isinstance(request, dict) else {},
                kwargs=kwargs,
                text=(text or ""),
                tool_key="ideation",
                cli_flag_re=CLI_FLAG_RE_DEFAULT,
                short_to_long=SHORT_TO_LONG,
                list_keys=LIST_KEYS,
                int_keys=INT_KEYS,
                bool_keys=BOOL_KEYS,
                topic_key=TOPIC_KEY,
            )

        history_backfill_used = False

        if history and (not flags.get("topic")):
            for msg in reversed(history):
                if msg.get("role") == "user":
                    hx = extract_cli_any(
                        request={},
                        kwargs={},
                        text=msg.get("content") or "",
                        tool_key="ideation",
                        cli_flag_re=CLI_FLAG_RE_DEFAULT,
                        short_to_long=SHORT_TO_LONG,
                        list_keys=LIST_KEYS,
                        int_keys=INT_KEYS,
                        bool_keys=BOOL_KEYS,
                        topic_key=TOPIC_KEY,
                    )
                    if hx.get("topic"):
                        flags.update(hx)
                        history_backfill_used = True
                        break

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🧭 TOOL][💡 IDEATION] flags_resolved",
            extra={
                "tool_key": self.key,
                "flags_source": flags_source,
                "history_backfill_used": history_backfill_used,
                "flag_keys": list(flags.keys()),
                "topic_present": bool(flags.get("topic")),
                "mode": flags.get("mode"),
                "platforms": flags.get("platforms"),
                "num_ideas": flags.get("num_ideas"),
            },
        )

        opts = parse_flags(flags, kwargs, cfg, text)

        self._last_topic = opts["topic"]
        self._last_num_ideas = opts["num_ideas"]
        self._last_mode = opts["mode"]
        self._last_brand = opts["brand"]
        self._last_frameworks = opts["frameworks"]
        self._last_seed = opts["seed"]
        self._last_max_tags_per_platform = opts["max_hashtags_per_platform"]
        self._last_platforms = opts["platforms"]

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[📚 TOOL][💡 IDEATION] opts_normalized",
            extra={
                "tool_key": self.key,
                "topic": self._last_topic,
                "mode": self._last_mode,
                "num_ideas": self._last_num_ideas,
                "brand": self._last_brand,
                "frameworks": self._last_frameworks,
                "platforms": self._last_platforms,
                "seed": self._last_seed,
                "max_tags_per_platform": self._last_max_tags_per_platform,
            },
        )

        system_msg = compose_system_message(cfg=cfg, opts=opts)

        bundle = await assemble_context_bundle(
            system_msg=system_msg,
            opts=opts,
            cfg=cfg,
            history=history,
            session_id=session_id,
            tool_key=self.key,
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[✅ TOOL][💡 IDEATION] provide_context_success",
            extra={
                "tool_key": self.key,
                "topic": self._last_topic,
                "mode": self._last_mode,
                "message_count": len(bundle.get("messages", [])),
                "evidence_count": len(bundle.get("evidence", [])),
                "has_user_overrides": bool(bundle.get("user_overrides")),
            },
        )

        return bundle

    def format_result(self, data: object):
        ctx = {
            "topic": (self._last_topic or ""),
            "mode": (self._last_mode or "cluster"),
            "platforms": list(self._last_platforms or []),
            "frameworks": list(self._last_frameworks or []),
            "num_ideas": int(self._last_num_ideas or 12),
            "seed": self._last_seed,
            "max_tags_per_platform": self._last_max_tags_per_platform,
            "brand": getattr(self, "_last_brand", None),
        }
        cfg_caps = getattr(self.config, "platform_caps", None) or {}

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🧾 TOOL][💡 IDEATION] format_result_start",
            extra={
                "tool_key": self.key,
                "topic": ctx.get("topic"),
                "mode": ctx.get("mode"),
                "platform_count": len(ctx.get("platforms", [])),
                "framework_count": len(ctx.get("frameworks", [])),
                "num_ideas": ctx.get("num_ideas"),
                "has_platform_caps": bool(cfg_caps),
            },
        )

        result = format_ideation_result(
            data,
            ctx=ctx,
            cfg_platform_caps=cfg_caps,
            trigger_pattern=PATTERN_IDEATE,
        )

        answer = (
            (((result or {}).get("result") or {}).get("answer"))
            if isinstance(result, dict)
            else None
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[✅ TOOL][💡 IDEATION] format_result_success",
            extra={
                "tool_key": self.key,
                "answer_len": len(answer or ""),
            },
        )

        return result
