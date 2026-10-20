from __future__ import annotations

import json
from typing import Any, TypedDict

from src.domains.agentverse.utils.estimate_tokens import estimate_tokens

from .constants import GLOBAL_HANDLE_RE, MULTI_SPACE_RE


class AuditorFormatterCtx(TypedDict, total=False):
    domain: str | None
    pack_ids: list[str]
    raw_text: str
    strict: bool | None
    rule_ids: list[str]


def _usage_stub(text: str) -> dict:
    out = estimate_tokens(text)
    return {
        "usage": {
            "latency_ms": None,
            "model": None,
            "provider": None,
            "tokens": {"input": 0, "output": out, "total": out},
            "estimated": True,
        }
    }


def _safe_json_dump(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return str(obj)


def _normalize_severity(s: Any) -> str:
    if not s:
        return "info"
    s = str(s).lower().strip()
    if s not in ("info", "warn", "warning", "error"):
        return "info"
    if s == "warning":
        return "warn"
    return s


def _compute_by_severity(issues: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"error": 0, "warn": 0, "info": 0}
    for issue in issues:
        sev = _normalize_severity(issue.get("severity"))
        if sev in counts:
            counts[sev] += 1
    return counts


def _compute_overall_risk(by_severity: dict[str, int]) -> str:
    e = by_severity.get("error", 0)
    w = by_severity.get("warn", 0)

    if e == 0 and w == 0:
        return "none"
    if e == 0 and w <= 2:
        return "low"
    if e <= 2:
        return "medium"
    return "high"


def _scrub_text(text: str) -> str:
    # Strip @handles and collapse whitespace like in ideation
    text = GLOBAL_HANDLE_RE.sub(" ", text or "")
    text = MULTI_SPACE_RE.sub(" ", text).strip()
    return text


def format_auditor_result(
    data: object,
    *,
    ctx: AuditorFormatterCtx,
) -> dict[str, Any]:
    """
    Normalize whatever the model produced into the canonical auditor JSON:

      {
        "summary": {...},
        "issues": [...],
        "legal_statement": "...",
        "guidelines_url": "...",
        "raw_text": "...",
        "usage": {...}
      }

    It is tolerant to:
    - Dicts already close to the target shape
    - Strings containing JSON (we'll try to parse them)
    - Plain-text outputs (wrapped into a single info issue)
    """
    domain = ctx.get("domain")
    pack_ids = ctx.get("pack_ids") or []
    raw_text_ctx = ctx.get("raw_text") or ""
    strict = ctx.get("strict")
    rule_ids = ctx.get("rule_ids") or []

    # -------- NEW: attempt to parse JSON if the model returned a string --------
    if isinstance(data, str):
        s = data.strip()
        # Heuristic: looks like JSON object
        if s.startswith("{") and s.endswith("}"):
            try:
                parsed = json.loads(s)
            except Exception:
                parsed = None
            else:
                if isinstance(parsed, dict):
                    data = parsed  # upgrade to dict so branch 1 will handle it

    # -------- branch 1: dict-like output --------
    if isinstance(data, dict):
        issues = data.get("issues") or data.get("green_claim_report") or []
        if not isinstance(issues, list):
            issues = [issues]

        summary = data.get("summary") or {}
        if not isinstance(summary, dict):
            summary = {}

        summary.setdefault("domain", domain)
        summary.setdefault("pack_ids", pack_ids)
        summary.setdefault("total_issues", len(issues))

        if "strict" not in summary and strict is not None:
            summary["strict"] = strict
        if "rule_ids" not in summary and rule_ids:
            summary["rule_ids"] = rule_ids

        by_severity = summary.get("by_severity")
        if not isinstance(by_severity, dict):
            by_severity = _compute_by_severity(issues)
            summary["by_severity"] = by_severity

        if "overall_risk" not in summary:
            summary["overall_risk"] = _compute_overall_risk(by_severity)

        if "green_claim_found" not in summary:
            summary["green_claim_found"] = bool(issues) if domain == "green_claims" else None

        legal_stmt = data.get("legal_statement")
        guidelines_url = data.get("guidelines") or data.get("guidelines_url")
        raw_text = data.get("raw_text") or raw_text_ctx

        payload = {
            "summary": summary,
            "issues": issues,
            "legal_statement": legal_stmt,
            "guidelines_url": guidelines_url,
            "raw_text": _scrub_text(raw_text),
        }

        payload["usage"] = _usage_stub(_safe_json_dump(payload))
        return payload

    # -------- branch 2: plain text / anything else (unchanged) --------
    safe = _safe_json_dump(data)
    safe = _scrub_text(safe)

    issue = {
        "rule_id": "freeform-output",
        "severity": "info",
        "title": "Non-structured auditor output",
        "snippet": safe,
        "explanation": "Model returned plain text instead of structured JSON.",
        "guidelines_reference": [],
        "suggested_fix": None,
        "tags": ["tooling", "fallback"],
    }

    by_severity = _compute_by_severity([issue])

    payload = {
        "summary": {
            "domain": domain,
            "pack_ids": pack_ids,
            "total_issues": 1,
            "by_severity": by_severity,
            "overall_risk": _compute_overall_risk(by_severity),
            "strict": strict,
            "rule_ids": rule_ids,
        },
        "issues": [issue],
        "legal_statement": None,
        "guidelines_url": None,
        "raw_text": _scrub_text(raw_text_ctx),
    }

    payload["usage"] = _usage_stub(_safe_json_dump(payload))
    return payload
