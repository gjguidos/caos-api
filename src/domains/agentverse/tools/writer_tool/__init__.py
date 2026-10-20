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
from src.domains.agentverse.tools._helpers.rule_context import (
    ResolvedRuleContext,
    resolve_rule_context,
)
from src.domains.agentverse.tools._helpers.trigger_cli_parser import (
    CLI_FLAG_RE_DEFAULT,
    extract_cli_any,
)
from src.domains.agentverse.tools.base import BaseTool
from src.domains.agentverse.tools.writer_tool.config import WriterToolConfig
from src.domains.agentverse.tools.writer_tool.constants import (
    BOOL_KEYS,
    INT_KEYS,
    LIST_KEYS,
)
from src.domains.agentverse.tools.writer_tool.flags import parse_flags
from src.domains.agentverse.tools.writer_tool.formatter import format_writer_result
from src.domains.agentverse.tools.writer_tool.messages import assemble_context_bundle
from src.domains.agentverse.tools.writer_tool.spec import writer_tool
from src.domains.agentverse.tools.writer_tool.sys_message import build_writer_system_message


@tool_registry.bind_spec(writer_tool)
class WriterTool(BaseTool):
    name = "Writer Tool"
    key = "writer"
    description = (
        "Drafts, rewrites, and polishes copy according to role, style, mood, "
        "rule packs, and length constraints."
    )
    version = "0.1.0"
    tool_class = "writer"
    required_dependencies: list[str] = []
    capabilities = ("writing", "copy", "editing")

    parameters: dict[str, Any] = {
        "text": {"type": "string", "required": False},
        "mode": {"type": "string", "required": False},
        "rule_packs": {"type": "array", "required": False, "items": {"type": "string"}},
        "session_id": {"type": "string", "required": False},
        "history": {"type": "array", "required": False},
        "policy": {"type": "object", "required": False},
        "request": {"type": "object", "required": False},
    }

    _last_opts: dict[str, Any] = {}
    _last_text: str = ""
    _last_rule_packs: list[str] = []

    def __init__(self, config: WriterToolConfig | dict | None = None):
        cfg = self._normalize_config(config)

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🛠️ TOOL][✍️ WRITER] init",
            extra={
                "tool_key": self.key,
                "tool_name": self.name,
                "version": self.version,
                "config_type": type(cfg).__name__,
            },
        )

        super().__init__(config=cfg)

    def _normalize_config(self, config: Any) -> WriterToolConfig:
        if isinstance(config, WriterToolConfig):
            return config
        if isinstance(config, BaseModel):
            return WriterToolConfig.model_validate(config)
        if isinstance(config, Mapping):
            return WriterToolConfig(**dict(config))
        if isinstance(config, SimpleNamespace):
            return WriterToolConfig(**vars(config))
        if isinstance(config, dict) or config is None:
            return WriterToolConfig(**(config or {}))
        raise TypeError(f"WriterTool config must be dict/BaseModel/namespace; got {type(config)}")

    @staticmethod
    def _pack_label(p: Any) -> str:
        if isinstance(p, str):
            return p
        if isinstance(p, Mapping):
            return p.get("id") or p.get("name") or p.get("slug") or p.get("key") or repr(p)
        for attr in ("id", "name", "slug", "key", "pack_id"):
            if hasattr(p, attr):
                val = getattr(p, attr)
                if isinstance(val, str) and val:
                    return val
        return repr(p)

    @staticmethod
    def _extract_flags(
        *, cfg: WriterToolConfig, cli_flags: dict | None, request: Any, kwargs: dict, raw_text: str
    ) -> dict:
        incoming = dict(cli_flags or {})
        req_dict = (request or {}) if isinstance(request, dict) else {}
        raw_for_cli = raw_text or (req_dict.get("raw_request") or req_dict.get("request") or "")

        reparsed = extract_cli_any(
            request=req_dict,
            kwargs=kwargs,
            text=raw_for_cli,
            tool_key="writer",
            cli_flag_re=CLI_FLAG_RE_DEFAULT,
            list_keys=LIST_KEYS,
            int_keys=INT_KEYS,
            bool_keys=BOOL_KEYS,
            topic_key="text",
        )

        if not incoming:
            return reparsed

        merged = {**reparsed, **incoming}

        incoming_text = incoming.get("text")
        reparsed_text = reparsed.get("text")

        if isinstance(reparsed_text, str) and len(reparsed_text.strip()) > len(
            str(incoming_text or "").strip()
        ):
            merged["text"] = reparsed_text

        return merged

    @staticmethod
    def _apply_explicit_args(
        flags: dict,
        *,
        raw_text: str,
        mode: str | None,
        rule_packs: list[str] | None,
    ) -> dict:
        if mode is not None:
            flags["mode"] = mode
        if rule_packs is not None:
            flags["rule_packs"] = rule_packs
        return flags

    @staticmethod
    def _get_raw_for_parser(*, raw_text: str, request: Any) -> str:
        req_dict = (request or {}) if isinstance(request, dict) else {}
        return raw_text or (req_dict.get("raw_request") or req_dict.get("request") or "")

    def _resolve_rules(
        self,
        *,
        agent: Any,
        opts: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> ResolvedRuleContext:
        del context
        override_packs = list(opts.get("rule_packs") or []) or None
        return resolve_rule_context(
            agent_pack_ids=list(getattr(agent, "rule_pack_ids", []) or []),
            override_pack_ids=override_packs,
        )

    def _normalize_inputs_and_mode(
        self,
        *,
        opts: dict[str, Any],
        cfg: WriterToolConfig,
        raw_text: str,
        raw_for_parser: str,
    ) -> tuple[str, str]:
        del raw_text, raw_for_parser  # mode no longer inferred from raw text
        text_val = str(opts.get("text") or "").strip()
        raw_mode = str(opts.get("mode") or cfg.default_mode or "write").strip().lower()
        return text_val, raw_mode

    async def provide_context(
        self,
        *,
        text: str = "",
        mode: str | None = None,
        rule_packs: list[str] | None = None,
        session_id: str | None = None,
        history: list[dict[str, str]] | None = None,
        context: dict[str, Any] | None = None,
        policy: dict[str, Any] | None = None,
        request: Any | None = None,
        cli_flags: dict | None = None,
        args: dict | None = None,
        **kwargs,
    ) -> dict:
        cfg: WriterToolConfig = self.config  # type: ignore

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🛠️ TOOL][✍️ WRITER] provide_context_start",
            extra={
                "tool_key": self.key,
                "has_text": bool(text),
                "text_len": len(text or ""),
                "mode_arg": mode,
                "rule_packs_arg": rule_packs or [],
                "has_history": bool(history),
                "history_count": len(history or []),
                "has_policy": bool(policy),
                "has_request": request is not None,
                "cli_flag_keys": list((cli_flags or {}).keys()),
            },
        )

        flags = self._extract_flags(
            cfg=cfg, cli_flags=cli_flags, request=request, kwargs=kwargs, raw_text=text
        )
        flags = self._apply_explicit_args(
            flags,
            raw_text=text,
            mode=mode,
            rule_packs=rule_packs,
        )

        raw_for_parser = self._get_raw_for_parser(raw_text=text, request=request)
        opts: dict[str, Any] = parse_flags(flags, args, cfg, raw_for_parser)

        agent = getattr(self, "agent", None)
        rule_ctx = self._resolve_rules(agent=agent, opts=opts, context=context)
        resolved_packs = rule_ctx.packs
        resolved_rules = rule_ctx.rules

        pack_labels = [self._pack_label(p) for p in (resolved_packs or [])]

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            f"[📚 TOOL][✍️ WRITER] rules_resolved flags: {flags}",
            extra={
                "tool_key": self.key,
                "resolved_pack_labels": pack_labels,
                "resolved_pack_count": len(resolved_packs or []),
                "resolved_rule_count": len(resolved_rules or []),
                "selected_pack_refs": rule_ctx.selected_pack_refs,
                "unresolved_pack_refs": rule_ctx.unresolved_pack_refs,
                "used_shared_rule_context": False,
                "agent_type": type(agent).__name__ if agent is not None else None,
            },
        )

        text_val, eff_mode = self._normalize_inputs_and_mode(
            opts=opts,
            cfg=cfg,
            raw_text=text,
            raw_for_parser=raw_for_parser,
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🧭 TOOL][✍️ WRITER] inputs_normalized",
            extra={
                "tool_key": self.key,
                "raw_text": text,
                "raw_for_parser": raw_for_parser,
                "flags": flags,
                "parsed_text": opts.get("text"),
                "final_text_len": len(text_val or ""),
                "effective_mode": eff_mode,
            },
        )

        self._last_opts = dict(opts)
        self._last_text = text_val
        self._last_rule_packs = pack_labels

        system_msg = build_writer_system_message(
            rules=resolved_rules,
            packs=resolved_packs,
            tone_of_voice=opts.get("tone_of_voice"),
            style=opts.get("style"),
            mood=opts.get("mood"),
            audience=opts.get("audience"),
        )

        if text_val:
            opts["text"] = text_val
        else:
            opts.pop("text", None)

        opts["mode"] = eff_mode

        bundle = await assemble_context_bundle(
            system_msg=system_msg,
            opts=opts,
            cfg=cfg,
            history=history,
            session_id=session_id,
            tool_key=self.key,
        )

        bundle.setdefault("evidence", [])
        bundle["evidence"].append(
            {
                "type": "rule_context",
                "rule_packs": self._last_rule_packs,
                "rules": [
                    r.model_dump() if hasattr(r, "model_dump") else repr(r) for r in resolved_rules
                ],
            }
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[✅ TOOL][✍️ WRITER] provide_context_success bundle",
            extra={
                "tool_key": self.key,
                "effective_mode": eff_mode,
                "text_len": len(text_val or ""),
                "rule_pack_count": len(self._last_rule_packs),
                "evidence_count": len(bundle.get("evidence", [])),
                "message_count": len(bundle.get("messages", [])),
            },
        )

        return bundle

    def format_result(self, data: object):
        ctx = {"raw_text": self._last_text, **(self._last_opts or {})}
        payload = format_writer_result(data, ctx=ctx)

        answer = payload.get("text", "")
        usage = payload.get("usage", {})
        meta = payload.get("meta", {})

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[✅ TOOL][✍️ WRITER] format_result",
            extra={
                "tool_key": self.key,
                "answer_len": len(answer or ""),
                "has_usage": bool(usage),
                "meta_keys": list(meta.keys()) if isinstance(meta, dict) else [],
            },
        )

        return {
            "result": {
                "answer": answer,
                "addons": {
                    "usage": usage,
                    "writer_meta": meta,
                },
            }
        }
