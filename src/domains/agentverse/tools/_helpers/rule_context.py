from __future__ import annotations

from dataclasses import dataclass

from src.domains.agentverse.agents.roles.rules.rulepack_spec import RulePackSpec, RuleSpec
from src.domains.agentverse.registries import rulepack_registry_instance as rulepack_registry
from src.domains.agentverse.utils.strip_quotes import strip_quotes


@dataclass
class ResolvedRuleContext:
    packs: list[RulePackSpec]
    rules: list[RuleSpec]
    selected_pack_refs: list[str]
    unresolved_pack_refs: list[str]
    domain: str | None = None


def _normalize_pack_ref(value: str) -> str:
    return strip_quotes(str(value or "")).strip().lower().replace("-", "_").replace(" ", "_")


def _stamp_rule_with_pack(rule: RuleSpec, pack: RulePackSpec) -> RuleSpec:
    if rule.domain and rule.pack_id:
        return rule
    data = rule.model_dump()
    if not data.get("domain"):
        data["domain"] = pack.domain
    if not data.get("pack_id"):
        data["pack_id"] = pack.name
    return RuleSpec(**data)


def resolve_rule_context(
    *,
    agent_pack_ids: list[str],
    override_pack_ids: list[str] | None = None,
) -> ResolvedRuleContext:
    """
    Resolve rule packs for a tool call.

    Uses override_pack_ids (from --rule-pack CLI flag) when provided, otherwise
    falls back to the agent's configured pack IDs.
    """
    if override_pack_ids is not None:
        pack_ids = [_normalize_pack_ref(ref) for ref in override_pack_ids if ref]
    else:
        pack_ids = list(agent_pack_ids or [])

    pack_ids = [p for p in pack_ids if p]

    if not pack_ids:
        return ResolvedRuleContext(
            packs=[],
            rules=[],
            selected_pack_refs=[],
            unresolved_pack_refs=[],
        )

    packs: list[RulePackSpec] = []
    unresolved: list[str] = []

    for pack_id in pack_ids:
        try:
            packs.append(rulepack_registry.get(pack_id))
        except KeyError:
            unresolved.append(pack_id)

    rules = [_stamp_rule_with_pack(rule, pack) for pack in packs for rule in pack.rules]

    return ResolvedRuleContext(
        packs=packs,
        rules=rules,
        selected_pack_refs=[p.name for p in packs],
        unresolved_pack_refs=unresolved,
    )
