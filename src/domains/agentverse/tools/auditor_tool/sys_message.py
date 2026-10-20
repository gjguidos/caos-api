# src/domains/agentverse/tools/auditor_tool/sys_message.py
from __future__ import annotations

from collections.abc import Iterable

from src.domains.agentverse.agents.roles.rules.rulepack_spec import RulePackSpec, RuleSpec

AUDITOR_JSON_CONTRACT = r"""
You MUST respond with a single JSON object with the following shape and nothing else:

{
  "summary": {
    "domain": "<string, e.g. 'green_claims'>",
    "pack_ids": ["<rule_pack_id>", "..."],
    "total_issues": <integer>,
    "by_severity": {
      "error": <integer>,
      "warn": <integer>,
      "info": <integer>
    },
    "overall_risk": "<none|low|medium|high>"
  },
  "issues": [
    {
      "rule_id": "<id of the rule, e.g. 'clarity-specificity'>",
      "severity": "<info|warn|error>",
      "title": "<short human label for this issue>",
      "statement": "<the problematic text, EXACTLY as written by the user>",
      "snippet": "<short excerpt if helpful>",
      "explanation": "<why this is a problem in plain language>",
      "guidelines_reference": ["<optional list of guideline points or references>"],
      "suggested_fix": "<how to improve the text without inventing facts>",
      "tags": ["<optional tags, e.g. 'green_claims','legal'>"]
    }
  ],
  "legal_statement": "<optional closing statement from Legal>",
  "guidelines_url": "<optional URL to guidelines>",
  "raw_text": "<the full user input text or empty string if not required>"
}
"""


def _render_rules_block(rules: Iterable[RuleSpec]) -> str:
    lines: list[str] = []
    for r in rules:
        lines.append(
            f"- id={r.id!r} | severity={r.severity!r} | applies_to={r.applies_to!r} "
            f"| domain={r.domain!r} | pack_id={r.pack_id!r}\n  {r.description}"
        )
    if not lines:
        return "No explicit rules were provided. In that case, be conservative and flag potential risks, but say that the rule set is empty."
    return "\n".join(lines)


def _merge_auditor_config(packs: Iterable[RulePackSpec]) -> dict:
    """
    Merge auditor-specific metadata from rule packs.
    Later you can make this smarter (first-pack-wins, etc.).
    """
    cfg: dict = {
        "guidelines_url": None,
        "legal_statement": None,
        "domain_instructions": None,
    }
    for p in packs or []:
        auditor_cfg = (p.default_config or {}).get("auditor") or {}
        for key in ("guidelines_url", "legal_statement", "domain_instructions"):
            if auditor_cfg.get(key) and not cfg.get(key):
                cfg[key] = auditor_cfg[key]
    return cfg


def build_auditor_system_message(
    *,
    domain: str,
    rules: list[RuleSpec],
    packs: list[RulePackSpec],
    strict: bool,
) -> str:
    """
    General auditor system message builder.
    - domain: e.g. "green_claims", "gdpr"
    - rules: the resolved RuleSpec list for this agent/context
    - packs: the RulePackSpec objects backing those rules
    - strict: if True, prefer over-flagging to under-flagging (in instructions wording)
    """
    rules_block = _render_rules_block(rules)
    pack_cfg = _merge_auditor_config(packs)

    domain_instructions = pack_cfg.get("domain_instructions") or (
        f"You are auditing text for the domain '{domain}'. Use the rules below as your sole authority."
    )

    guidelines_url = pack_cfg.get("guidelines_url") or ""
    legal_statement = pack_cfg.get("legal_statement") or ""

    strict_note = (
        "You are in STRICT mode: when in doubt, err on the side of flagging a potential issue, "
        "but clearly mark uncertainty in the explanation."
        if strict
        else "You are in NORMAL mode: flag clear issues and avoid over-flagging borderline cases."
    )

    msg = f"""
Role: You are a rules-based auditor for domain '{domain}'.
Primary Goal: Analyze the provided text against the rule set and produce a structured JSON risk/compliance report.

{strict_note}

Domain-specific instructions:
{domain_instructions}

Rule set in force (do not invent extra rules):
{rules_block}

Output contract:
{AUDITOR_JSON_CONTRACT}

Guidelines URL (if applicable): {guidelines_url or "none provided"}
Legal statement (if applicable): {legal_statement or "none provided"}

General behavior:
- Only use the rules listed above plus the explicit output contract.
- Do not invent regulatory requirements that are not implied by the rules.
- Be concrete and cite exactly which part of the text is problematic in 'statement'.
- If no issues are found, return "issues": [] and set "overall_risk" to "none".
- You MUST return only JSON (no markdown, no prose, no comments).
    """.strip()

    return msg
