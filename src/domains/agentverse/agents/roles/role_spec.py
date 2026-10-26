from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from src.domains.agentverse.agents.roles.rules.rulepack_spec import RuleSpec
from src.domains.agentverse.entities.resource_ownership import ResourceOwnership
from src.domains.agentverse.utils.canonicalize import require_canonical_key

# Orchestrator phase handler signature: (ctx, role_spec) -> ctx
PhaseHandler = Callable[[dict, "RoleSpec"], Awaitable[dict]]


class RoleBinding(BaseModel):
    name: str  # e.g. "fe-writer", "compliance-auditor"
    version: str | None = None  # e.g. "v1", "v2", "2025-11-18"

    # Optional per-agent knobs for that role:
    config: dict[str, Any] = Field(default_factory=dict)
    rule_packs: list[str] = Field(default_factory=list)  # IDs in RulePack registry/DB
    rule_overrides: dict[str, Any] | None = None  # only deltas, not full rules


class RolePolicy(BaseModel):
    name: str
    version: str = "1.0.0"
    temperature: float = 0.3
    samples: int = 1
    quality_threshold: float = 0.8
    # room for future enterprise knobs (timeouts, retries, budgets, breaker…)
    extras: dict[str, Any] = Field(default_factory=dict)


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat() + "Z"


@dataclass
class PhaseSpec:
    """Pure-data description the FE can render for each phase."""

    title: str
    summary: str = ""
    timeout_ms: int | None = None
    retry: dict[str, Any] = field(
        default_factory=dict
    )  # e.g., {"max_attempts": 1, "backoff_ms": 0}
    consumes: list[str] = field(default_factory=list)  # state keys / inputs
    emits: list[str] = field(default_factory=list)  # state keys / outputs
    show_in_ui: bool = True  # FE toggle for visibility
    ui_hints: dict[str, Any] = field(default_factory=dict)  # {"show_plan_panel": True, ...}


# eq=False keeps identity equality (and therefore hashability): specs hold live
# handler callables and per-instance timestamps, so field-wise comparison is meaningless.
@dataclass(eq=False)
class RoleSpec:
    # Core
    key: str  # canonical, unique identifier (e.g. "general"); the FE addresses roles by this
    name: str
    version: str = "1.0.0"
    policy: Any = None  # pydantic model; must support .model_dump()
    handlers: dict[str, Any] = field(default_factory=dict)  # server-only callables (not serialized)
    rules: list[RuleSpec] | None = None
    agent_group: str | None = None
    agent_subclass: str | None = None
    capabilities: dict[str, Any] = field(default_factory=dict)

    # Rich, FE-friendly metadata (all optional)
    title: str | None = None
    summary: str | None = None
    description: str | None = None
    emoji: str | None = None  # "🧭"
    icon: str | None = None  # e.g., "lucide:compass"
    color: str | None = None  # e.g., "indigo" (design system token)
    banner_url: str | None = None
    tags: list[str] = field(default_factory=list)
    badges: list[str] = field(default_factory=list)  # "beta", "internal", "safe-by-default", etc.

    # Docs/links/i18n
    docs: dict[str, str] = field(
        default_factory=dict
    )  # {"overview": "...", "usage": "...", "changelog": "..."}
    links: dict[str, str] = field(default_factory=dict)  # {"community": "...", "source": "..."}
    i18n: dict[str, dict[str, str]] = field(
        default_factory=dict
    )  # {"sv-SE": {"title": "...", "summary": "..."}}

    # Examples the FE can render directly
    examples: dict[str, Any] = field(
        default_factory=lambda: {
            "sample_prompts": [],
            "sample_tasks": [],
            "sample_conversation": [],  # [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
        }
    )

    # Config surfaced to FE (Zod/JSON Schema compatible)
    config_schema: dict[str, Any] = field(default_factory=dict)
    default_config: dict[str, Any] = field(default_factory=dict)
    default_tools: list[str] = field(default_factory=list)

    # Safety/compat/telemetry/state/compute
    safety: dict[str, Any] = field(
        default_factory=dict
    )  # {"allowed": [...], "blocked": [...], "guardrails": {...}}
    compat: dict[str, Any] = field(
        default_factory=dict
    )  # {"min_app_version":"0.9.0", "deprecated": False}
    telemetry: dict[str, Any] = field(
        default_factory=dict
    )  # {"metrics": ["latency_ms","eval_score","refine_loops"]}
    state: dict[str, Any] = field(
        default_factory=dict
    )  # {"memory_strategy":"short_term", "keys":[...]}
    compute: dict[str, Any] = field(
        default_factory=dict
    )  # {"tier":"standard","max_tokens":4096,"est_tokens_per_turn":800}

    # Per-phase public descriptions (not the callables)
    phase_specs: dict[str, PhaseSpec] = field(default_factory=dict)

    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    ownership: ResourceOwnership | None = None

    def __post_init__(self) -> None:
        require_canonical_key(self.key, kind="Role", name=self.name)

    def to_metadata(self) -> dict[str, Any]:
        """JSON-serializable manifest for the FE; strips callables and dumps policy safely."""
        # Safe handler exposure — names only
        handler_names = {
            k: getattr(v, "__name__", str(v)) for k, v in (self.handlers or {}).items()
        }
        # Policy dump (support pydantic)
        policy_dump = (
            self.policy.model_dump() if hasattr(self.policy, "model_dump") else self.policy
        )

        # Convert nested dataclasses (e.g., PhaseSpec) to dicts
        def _coerce(v):
            if isinstance(v, list):
                return [_coerce(x) for x in v]
            if isinstance(v, dict):
                return {kk: _coerce(vv) for kk, vv in v.items()}
            if hasattr(v, "__dataclass_fields__"):
                return asdict(v)
            return v

        manifest = {
            "ownership": self.ownership,
            "key": self.key,
            "name": self.name,
            "version": self.version,
            "title": self.title or self.name.title(),
            "summary": self.summary,
            "description": self.description,
            "rules": [asdict(rule) for rule in (self.rules or [])],
            "emoji": self.emoji,
            "icon": self.icon,
            "color": self.color,
            "banner_url": self.banner_url,
            "tags": self.tags,
            "badges": self.badges,
            "docs": self.docs,
            "links": self.links,
            "i18n": self.i18n,
            "policy": policy_dump,
            "handlers_present": list(handler_names.keys()),
            "agent_group": self.agent_group,
            "agent_subclass": self.agent_subclass,
            "capabilities": self.capabilities,
            "examples": self.examples,
            "config_schema": self.config_schema,
            "default_config": self.default_config,
            "default_tools": self.default_tools,
            "safety": self.safety,
            "compat": self.compat,
            "telemetry": self.telemetry,
            "state": self.state,
            "compute": self.compute,
            "phase_specs": _coerce(self.phase_specs),
            "created_at": self.created_at,
            "updated_at": _now_iso(),
        }
        return manifest
