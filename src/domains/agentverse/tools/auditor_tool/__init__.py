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
from src.domains.agentverse.tools.auditor_tool.config import AuditorToolConfig
from src.domains.agentverse.tools.auditor_tool.constants import (
    BOOL_KEYS,
    INT_KEYS,
    LIST_KEYS,
    SHORT_TO_LONG,
    TEXT_KEY,
)
from src.domains.agentverse.tools.auditor_tool.flags import parse_flags
from src.domains.agentverse.tools.auditor_tool.formatter import (
    format_auditor_result,
)
from src.domains.agentverse.tools.auditor_tool.messages import (
    assemble_context_bundle,
)
from src.domains.agentverse.tools.auditor_tool.specs import auditor_tool
from src.domains.agentverse.tools.auditor_tool.sys_message import build_auditor_system_message
from src.domains.agentverse.tools.base import BaseTool


@tool_registry.bind_spec(auditor_tool)
class AuditorTool(BaseTool):
    """
    Generic rules-based content auditing tool.

    It inspects the active rule context and produces a structured
    compliance / risk report for the given text.
    """

    name = "Auditor Tool"
    key = "auditor"
    description = "Audits text against the agent's rule packs (green claims, GDPR, etc.)."
    version = "0.1.0"
    tool_class = "auditor"
    required_dependencies: list[str] = []
    capabilities = ("rule_audit", "compliance_check")

    parameters: dict[str, Any] = {
        "text": {
            "type": "string",
            "required": True,
            "description": "Text to audit against the active rule packs.",
        },
        "domain": {
            "type": "string",
            "required": False,
            "description": "Optional domain filter, e.g. 'green_claims', 'gdpr'.",
        },
        "pack_ids": {
            "type": "array",
            "required": False,
            "description": "Optional list of rule pack IDs to enforce.",
            "items": {"type": "string"},
        },
        "strict": {
            "type": "boolean",
            "required": False,
            "description": "If true, treat all warnings as errors and be maximally conservative.",
        },
        "session_id": {"type": "string", "required": False},
        "history": {"type": "array", "required": False},
        "policy": {"type": "object", "required": False},
        "request": {"type": "object", "required": False},
    }

    _last_domain: str | None = None
    _last_pack_ids: list[str] = []
    _last_strict: bool | None = None
    _last_text: str = ""
    _last_rule_ids: list[str] = []

    def __init__(self, config: AuditorToolConfig | dict | None = None):
        cfg = self._normalize_config(config)

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🛠️ TOOL][🧾 AUDITOR] init",
            extra={
                "tool_key": self.key,
                "tool_name": self.name,
                "version": self.version,
                "config_type": type(cfg).__name__,
            },
        )

        super().__init__(config=cfg)

    def _normalize_config(self, config: Any) -> AuditorToolConfig:
        if isinstance(config, AuditorToolConfig):
            return config
        if isinstance(config, BaseModel):
            return AuditorToolConfig.model_validate(config)
        if isinstance(config, Mapping):
            return AuditorToolConfig(**dict(config))
        if isinstance(config, SimpleNamespace):
            return AuditorToolConfig(**vars(config))
        if isinstance(config, dict) or config is None:
            return AuditorToolConfig(**(config or {}))
        raise TypeError(f"AuditorTool config must be dict/BaseModel/namespace; got {type(config)}")

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
    def _pack_domain(p: Any) -> str | None:
        if isinstance(p, Mapping):
            return p.get("domain") or p.get("category")
        for attr in ("domain", "category", "scope"):
            if hasattr(p, attr):
                val = getattr(p, attr)
                if isinstance(val, str) and val:
                    return val
        return None

    def _resolve_rules(
        self,
        *,
        agent: Any,
        opts: Mapping[str, Any],
        kwargs: dict[str, Any],
    ) -> ResolvedRuleContext:
        override_packs = list(opts.get("pack_ids") or []) or None
        return resolve_rule_context(
            agent_pack_ids=list(getattr(agent, "rule_pack_ids", []) or []),
            override_pack_ids=override_packs,
        )

    async def provide_context(
        self,
        *,
        text: str = "",
        domain: str | None = None,
        pack_ids: list[str] | None = None,
        strict: bool | None = None,
        session_id: str | None = None,
        history: list[dict[str, str]] | None = None,
        policy: dict[str, Any] | None = None,
        request: Any | None = None,
        cli_flags: dict | None = None,
        args: dict | None = None,
        **kwargs,
    ) -> dict:
        cfg: AuditorToolConfig = self.config  # type: ignore

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🛠️ TOOL][🧾 AUDITOR] provide_context_start",
            extra={
                "tool_key": self.key,
                "has_text": bool(text),
                "text_len": len(text or ""),
                "domain_arg": domain,
                "pack_ids_arg": pack_ids or [],
                "strict_arg": strict,
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
                tool_key="auditor",
                cli_flag_re=CLI_FLAG_RE_DEFAULT,
                short_to_long=SHORT_TO_LONG,
                list_keys=LIST_KEYS,
                int_keys=INT_KEYS,
                bool_keys=BOOL_KEYS,
                topic_key=TEXT_KEY,
            )

        if domain is not None:
            flags["domain"] = domain
        if pack_ids is not None:
            flags["pack_ids"] = pack_ids
        if strict is not None:
            flags["strict"] = strict
        if text:
            flags[TEXT_KEY] = text

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🧭 TOOL][🧾 AUDITOR] flags_resolved",
            extra={
                "tool_key": self.key,
                "flags_source": flags_source,
                "flag_keys": list(flags.keys()),
                "domain": flags.get("domain"),
                "pack_ids": flags.get("pack_ids"),
                "strict": flags.get("strict"),
                "has_text_flag": bool(flags.get(TEXT_KEY)),
            },
        )

        opts = parse_flags(flags, kwargs, cfg, text)
        agent = getattr(self, "agent", None)

        rule_ctx = self._resolve_rules(agent=agent, opts=opts, kwargs=kwargs)
        resolved_packs = rule_ctx.packs
        resolved_rules = rule_ctx.rules
        unresolved_pack_refs = rule_ctx.unresolved_pack_refs

        effective_pack_ids = list(rule_ctx.selected_pack_refs or [])
        pack_labels = [self._pack_label(p) for p in resolved_packs]

        requested_domain = (opts.get("domain") or cfg.default_domain or "").strip()
        if requested_domain:
            effective_domain = requested_domain
        else:
            pack_domains = {d for d in (self._pack_domain(p) for p in resolved_packs) if d}
            if len(pack_domains) == 1:
                effective_domain = next(iter(pack_domains))
            elif len(pack_domains) > 1:
                effective_domain = "multi"
            else:
                effective_domain = "generic"

        effective_strict = (
            bool(opts.get("strict"))
            if opts.get("strict") is not None
            else bool(getattr(cfg, "strict_default", False))
        )

        filtered_rules = list(resolved_rules or [])

        if requested_domain:
            filtered_rules = [
                r
                for r in filtered_rules
                if not getattr(r, "domain", None) or getattr(r, "domain", None) == requested_domain
            ]

        rule_ids = [getattr(r, "id", "") for r in filtered_rules if getattr(r, "id", None)]

        # Respect user-provided pack_ids by treating them as an allow-list.
        # If the user specified pack_ids, intersect them with effective_pack_ids
        # instead of broadening the selection.
        user_pack_ids = opts.get("pack_ids")
        if isinstance(user_pack_ids, list | tuple | set) and user_pack_ids:
            user_pack_ids_set = set(user_pack_ids)
            restricted_effective_pack_ids = [
                pack_id for pack_id in (effective_pack_ids or []) if pack_id in user_pack_ids_set
            ]
            effective_pack_ids = restricted_effective_pack_ids
            opts["pack_ids"] = restricted_effective_pack_ids
        else:
            opts["pack_ids"] = effective_pack_ids
        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[📚 TOOL][🧾 AUDITOR] rule_context_resolved",
            extra={
                "tool_key": self.key,
                "effective_domain": effective_domain,
                "effective_strict": effective_strict,
                "resolved_pack_labels": pack_labels,
                "resolved_pack_count": len(resolved_packs or []),
                "filtered_rule_count": len(filtered_rules),
                "effective_pack_ids": effective_pack_ids,
                "unresolved_pack_refs": unresolved_pack_refs,
                "rule_ids": rule_ids,
            },
        )

        self._last_domain = effective_domain
        self._last_pack_ids = effective_pack_ids
        self._last_strict = effective_strict
        self._last_text = str(opts.get("text") or text or "")
        self._last_rule_ids = rule_ids

        system_msg = build_auditor_system_message(
            domain=effective_domain,
            rules=filtered_rules,
            packs=resolved_packs,
            strict=effective_strict,
        )

        opts["domain"] = effective_domain
        opts["strict"] = effective_strict
        opts["text"] = self._last_text

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
                "domain": effective_domain,
                "pack_ids": effective_pack_ids,
                "unresolved_pack_refs": unresolved_pack_refs,
                "rules": [r.model_dump() for r in filtered_rules],
            }
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[✅ TOOL][🧾 AUDITOR] provide_context_success",
            extra={
                "tool_key": self.key,
                "effective_domain": effective_domain,
                "effective_strict": effective_strict,
                "text_len": len(self._last_text or ""),
                "rule_count": len(rule_ids),
                "evidence_count": len(bundle.get("evidence", [])),
                "message_count": len(bundle.get("messages", [])),
            },
        )

        return bundle

    def _render_human_message(self, report: dict[str, Any]) -> str:
        summary = report.get("summary") or {}
        overall = (summary.get("overall_risk") or "none").upper()

        issues = report.get("issues") or []

        def _sev(v: Any) -> str:
            return (str(v or "")).lower().strip()

        errors = [i for i in issues if _sev(i.get("severity")) == "error"]
        warns = [i for i in issues if _sev(i.get("severity")) == "warn"]
        infos = [i for i in issues if _sev(i.get("severity")) == "info"]

        total_errors = len(errors)
        total_warns = len(warns)
        total_infos = len(infos)

        lines: list[str] = []
        lines.append("**Environmental Claims Audit - Legal Summary**")
        lines.append("")
        lines.append(f"Overall risk assessment: **{overall}**")
        lines.append("")
        lines.append("Summary of identified issues:")
        lines.append(f"- Serious issues (errors): **{total_errors}**")
        lines.append(f"- Warnings: **{total_warns}**")
        lines.append(f"- Informational notes: **{total_infos}**")
        lines.append("")

        def render_group(
            heading: str,
            items: list[dict],
            severity_label: str,
        ) -> list[str]:
            block: list[str] = []
            block.append(f"### {heading}")
            block.append("")
            if not items:
                block.append(f"No {severity_label.lower()} identified in this review.")
                block.append("")
                return block

            for idx, item in enumerate(items, 1):
                rule_id = item.get("rule_id") or ""
                title = item.get("title") or "Issue"
                snippet = item.get("snippet") or ""
                explanation = item.get("explanation") or ""
                suggested_fix = item.get("suggested_fix")

                if rule_id:
                    block.append(f"{idx}. {title} (rule: {rule_id})")
                else:
                    block.append(f"{idx}. {title}")

                if snippet:
                    block.append(f"- Relevant claim: {snippet}")
                if explanation:
                    block.append(f"- Reasoning: {explanation}")
                if suggested_fix:
                    block.append(f"- Suggested adjustment: {suggested_fix}")

                block.append("")
            return block

        lines += render_group(
            heading="1. Serious issues (errors)",
            items=errors,
            severity_label="Errors",
        )
        lines += render_group(
            heading="2. Warnings",
            items=warns,
            severity_label="Warnings",
        )
        lines += render_group(
            heading="3. Informational notes",
            items=infos,
            severity_label="Informational notes",
        )

        return "\n".join(lines)

    def format_result(self, data: object):
        ctx = {
            "domain": self._last_domain,
            "pack_ids": list(self._last_pack_ids or []),
            "strict": bool(self._last_strict),
            "rule_ids": list(self._last_rule_ids or []),
            "text": self._last_text,
            "raw_text": self._last_text,
        }

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🧾 TOOL][🧾 AUDITOR] format_result_start",
            extra={
                "tool_key": self.key,
                "domain": self._last_domain,
                "strict": bool(self._last_strict),
                "pack_ids": list(self._last_pack_ids or []),
                "rule_count": len(self._last_rule_ids or []),
                "text_len": len(self._last_text or ""),
            },
        )

        report = format_auditor_result(data, ctx=ctx)
        summary = self._render_human_message(report)

        addons = {
            "reports": {
                self.key: report,
            },
            "usage": report.get("usage"),
        }

        issues = report.get("issues") or []
        summary_block = report.get("summary") or {}

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[✅ TOOL][🧾 AUDITOR] format_result_success",
            extra={
                "tool_key": self.key,
                "summary_len": len(summary or ""),
                "issue_count": len(issues),
                "overall_risk": summary_block.get("overall_risk"),
                "has_usage": bool(report.get("usage")),
            },
        )

        return {
            "result": {
                "answer": summary,
                "addons": addons,
            }
        }
