"""Versioned CATALOG-0.1 contracts (``caos.catalog.*`` v1.0.0).

Strict, frozen and extra-forbidden at every boundary. Requests carry no identity,
tenant, grant, provider or signal: the requester is supplied in-process from trusted
authority (``CatalogRequester``), never from request data.

Five decisions are kept apart and never collapse into one boolean:

* **existence** and **requester visibility** are only observable together: a returned
  entry exists *and* is visible; an unknown resource and a hidden one are the same
  ``unavailable`` resolution, so the catalog cannot be used to probe other tenants;
* **candidate compatibility** is a deterministic, advisory composition check against an
  Agent being composed (never a persisted Agent);
* **candidate attachment** says whether that candidate already references the resource;
* **execution authorization** is never evaluated: ``"not_evaluated"`` is the only value
  the contract can express. UTU permits and CPBX grants remain the only authority.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

CATALOG_CONTRACT_VERSION = "1.0.0"

CatalogKind = Literal["agent_type", "role", "soul", "tool", "workflow", "rulepack", "manuscript"]
CATALOG_KINDS: tuple[CatalogKind, ...] = (
    "agent_type",
    "role",
    "soul",
    "tool",
    "workflow",
    "rulepack",
    "manuscript",
)
# Catalog kinds backed by an AOSA client registry, mapped onto the registry name.
REGISTRY_KINDS: dict[str, str] = {
    "agent_type": "agent",
    "role": "role",
    "soul": "personality",
    "tool": "tool",
    "rulepack": "rulepack",
}

Key = Annotated[str, StringConstraints(min_length=1, max_length=128)]
Version = Annotated[str, StringConstraints(pattern=r"^[0-9A-Za-z.+-]{1,32}$")]
Text = Annotated[str, StringConstraints(max_length=500)]
Short = Annotated[str, StringConstraints(max_length=120)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


# --- References ---------------------------------------------------------------------------


class CatalogResourceRef(_Strict):
    """Typed, exact-version reference for Manuscript, Reasoning and Ptah bindings.

    A reference names a resource; it never implies visibility at a later time, attachment
    or execution. Consumers re-resolve before use. ``fingerprint`` is set only where the
    owning registry pins content (Manuscripts); registry versions are exact otherwise.
    """

    contract: Literal["caos.catalog.resource_ref"] = "caos.catalog.resource_ref"
    contract_version: Literal["1.0.0"] = "1.0.0"
    kind: CatalogKind
    key: Key
    version: Version
    fingerprint: Sha256 | None = None

    @property
    def address(self) -> str:
        return f"{self.key}@{self.version}"


# --- Safe presentation metadata (Ptah-ready, declarative only) -----------------------------


class FallbackIcon(_Strict):
    """Deterministic icon that always renders without media: kind glyph + monogram + hue."""

    style: Literal["monogram"] = "monogram"
    glyph: CatalogKind
    text: Annotated[str, StringConstraints(min_length=1, max_length=2)]
    hue: Annotated[int, Field(ge=0, le=359)]


class MediaRef(_Strict):
    """Reserved for a future governed asset service; CATALOG-0.1 never populates it."""

    asset_ref: Annotated[
        str, StringConstraints(pattern=r"^asset:[a-z0-9_-]{1,64}@sha256:[0-9a-f]{64}$")
    ]
    alt: Short


class CatalogIdentity(_Strict):
    title: Short
    summary: Text | None = None
    # A declared icon token or emoji: presentation only, never a URL.
    icon: Annotated[str, StringConstraints(max_length=64)] | None = None
    media: MediaRef | None = None
    fallback_icon: FallbackIcon
    category: Annotated[str, StringConstraints(max_length=64)] | None = None
    tags: Annotated[
        tuple[Annotated[str, StringConstraints(max_length=64)], ...], Field(max_length=16)
    ] = ()
    scope: Literal["global", "tenant"]


class ToolConfigOption(_Strict):
    """A reviewed, descriptive choice for a configuration field (string values only)."""

    value: Key
    label: Short
    description: Text | None = None


class ToolConfigField(_Strict):
    key: Key
    label: Short
    input: Annotated[str, StringConstraints(max_length=32)] = "text"
    required: bool = False
    help: Text | None = None
    options: Annotated[tuple[ToolConfigOption, ...], Field(max_length=32)] = ()


class ToolConfiguration(_Strict):
    """Agent-specific configuration knobs already reviewed as public (RG-02F)."""

    configurable: bool
    fields: Annotated[tuple[ToolConfigField, ...], Field(max_length=32)] = ()
    # ``agent_config_schema`` after ``public_schema`` sanitization; JSON Schema subset.
    config_schema: dict[str, Any] = Field(default_factory=dict)


class AgentTypeDetails(_Strict):
    kind: Literal["agent_type"] = "agent_type"
    agent_group: Short | None = None
    agent_subclass: Short | None = None


class RoleDetails(_Strict):
    kind: Literal["role"] = "role"
    agent_group: Short | None = None
    agent_subclass: Short | None = None
    # Only Tools the requester may read; protected requirements are never named.
    required_tools: Annotated[tuple[Key, ...], Field(max_length=64)] = ()
    optional_tools: Annotated[tuple[Key, ...], Field(max_length=64)] = ()


class SoulDetails(_Strict):
    kind: Literal["soul"] = "soul"


class ToolDetails(_Strict):
    kind: Literal["tool"] = "tool"
    tool_class: Short | None = None
    min_rulepacks: Annotated[int, Field(ge=0, le=64)] = 0
    configuration: ToolConfiguration


class WorkflowDetails(_Strict):
    kind: Literal["workflow"] = "workflow"
    input_count: Annotated[int, Field(ge=0)] = 0


class RulePackDetails(_Strict):
    kind: Literal["rulepack"] = "rulepack"
    domain: Short | None = None
    rule_count: Annotated[int, Field(ge=0)] = 0


class ManuscriptStage(_Strict):
    key: Key
    title: Short


class ManuscriptDetails(_Strict):
    kind: Literal["manuscript"] = "manuscript"
    artifact_contract: Short
    stages: Annotated[tuple[ManuscriptStage, ...], Field(max_length=64)] = ()


CatalogDetails = Annotated[
    AgentTypeDetails
    | RoleDetails
    | SoulDetails
    | ToolDetails
    | WorkflowDetails
    | RulePackDetails
    | ManuscriptDetails,
    Field(discriminator="kind"),
]


# --- Decisions ----------------------------------------------------------------------------


class CompatibilityIssue(_Strict):
    code: Annotated[str, StringConstraints(pattern=r"^[a-z_]+\.[a-z_]+$")]
    # Blocking issues are intrinsic to the pair; non-blocking ones resolve by changing
    # the candidate (adding a Tool or RulePack).
    blocking: bool
    subject: Key | None = None


CandidatePart = Literal["agent_type", "role"]


class CompatibilityDecision(_Strict):
    """Advisory only: ``data.validation`` (G3.3) remains authoritative for creation.

    ``compatible`` is unconditional: every applicable check ran on a candidate whose
    references all resolved. ``conditional`` means no issue was found but a check could
    not run because the candidate lacks a part (listed in ``unevaluated``). Candidate
    references that do not resolve for the requester (unknown and hidden alike) are
    reported per candidate field as ``candidate.reference_unavailable``, never by name
    or count.
    """

    advisory: Literal[True] = True
    status: Literal[
        "compatible", "conditional", "requires_changes", "incompatible", "not_evaluated"
    ]
    issues: tuple[CompatibilityIssue, ...] = ()
    unevaluated: tuple[CandidatePart, ...] = ()


class AttachmentDecision(_Strict):
    status: Literal["attached", "not_attached", "not_evaluated"]


class CatalogDecisions(_Strict):
    existence: Literal["exists"] = "exists"
    requester_visibility: Literal["visible"] = "visible"
    candidate_compatibility: CompatibilityDecision
    candidate_attachment: AttachmentDecision
    # The catalog cannot express an execution grant.
    execution_authorization: Literal["not_evaluated"] = "not_evaluated"


# --- Entries, requests and results ---------------------------------------------------------


class CatalogEntry(_Strict):
    contract: Literal["caos.catalog.entry"] = "caos.catalog.entry"
    contract_version: Literal["1.0.0"] = "1.0.0"
    ref: CatalogResourceRef
    identity: CatalogIdentity
    details: CatalogDetails
    decisions: CatalogDecisions
    # Digest of ref + identity + details: stable for identical registries and requester
    # visibility; independent of candidate decisions.
    descriptor_digest: Sha256


class CatalogCandidate(_Strict):
    """An Agent being composed (e.g. a draft's ``AgentCreationInput`` references).

    Advisory input only: never a persisted Agent, approval or grant. Parts the requester
    cannot see are ignored, so compatibility cannot reveal hidden resources.
    """

    type: Key | None = None
    role_name: Key | None = None
    personality_key: Key | None = None
    tools: Annotated[tuple[Key, ...], Field(max_length=64)] = ()
    workflows: Annotated[tuple[Key, ...], Field(max_length=64)] = ()
    rule_pack_ids: Annotated[tuple[Key, ...], Field(max_length=64)] = ()


class CatalogListRequest(_Strict):
    contract: Literal["caos.catalog.list"] = "caos.catalog.list"
    contract_version: Literal["1.0.0"] = "1.0.0"
    kind: CatalogKind
    candidate: CatalogCandidate | None = None
    # Drops ``incompatible`` entries; requires a candidate.
    compatible_only: bool = False
    after: Key | None = None
    limit: Annotated[int, Field(ge=1, le=100)] = 50


class CatalogListResult(_Strict):
    contract: Literal["caos.catalog.list_result"] = "caos.catalog.list_result"
    contract_version: Literal["1.0.0"] = "1.0.0"
    kind: CatalogKind
    entries: tuple[CatalogEntry, ...]
    next_after: Key | None = None


class CatalogResolveRequest(_Strict):
    contract: Literal["caos.catalog.resolve"] = "caos.catalog.resolve"
    contract_version: Literal["1.0.0"] = "1.0.0"
    kind: CatalogKind
    value: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    version: Version | None = None
    candidate: CatalogCandidate | None = None


class CatalogResolution(_Strict):
    contract: Literal["caos.catalog.resolution"] = "caos.catalog.resolution"
    contract_version: Literal["1.0.0"] = "1.0.0"
    # ``unavailable`` covers unknown, hidden, foreign-tenant and malformed references alike.
    # ``version_unavailable`` is only reported for a resource the requester can see.
    status: Literal["resolved", "unavailable", "version_unavailable"]
    entry: CatalogEntry | None = None
