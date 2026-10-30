"""Manuscript definition contract (``caos.manuscript`` v1) and its predicate language.

A Manuscript is data, never code: typed fields, ordered stages, a closed predicate
language and optional Markdown guidance for expression. Every definition is validated
structurally and type-checked when it is built; evaluation is pure, bounded and needs no
I/O. Guidance Markdown is never parsed for instructions and carries no authority.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    StringConstraints,
    model_validator,
)

from src.base.operations.v2.admission import sha256_hex

MANUSCRIPT_CONTRACT = "caos.manuscript"
MAX_PREDICATE_DEPTH = 8
MAX_PREDICATE_NODES = 64
MAX_STAGES = 32
MAX_FIELDS = 64
# The only capabilities a Manuscript may declare as its handoff. A declaration is
# metadata: it never grants, registers or invokes anything.
ALLOWED_HANDOFFS = frozenset({("agent.draft.propose", "1.0.0")})

Slug = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
SemVer = Annotated[str, StringConstraints(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")]
Scalar = StrictStr | StrictBool
CatalogKind = Literal["agent", "role", "personality", "tool", "workflow", "rulepack"]
FieldType = Literal["text", "enum", "boolean", "text_list", "catalog_ref", "catalog_ref_list"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


# --- Predicates ------------------------------------------------------------------------------


class Always(_Strict):
    op: Literal["always"]


class Present(_Strict):
    op: Literal["present"]
    field: Slug


class Equals(_Strict):
    op: Literal["equals"]
    field: Slug
    value: Scalar


class In(_Strict):
    op: Literal["in"]
    field: Slug
    values: Annotated[list[StrictStr], Field(min_length=1, max_length=64)]


class CatalogContains(_Strict):
    """True when the field holds catalog reference(s) accepted against the requester's
    catalog. Membership is checked when the answer is accepted, so evaluation is pure."""

    op: Literal["catalog_contains"]
    field: Slug


class All(_Strict):
    op: Literal["all"]
    of: Annotated[list[Predicate], Field(min_length=1, max_length=16)]


class AnyOf(_Strict):
    op: Literal["any"]
    of: Annotated[list[Predicate], Field(min_length=1, max_length=16)]


class Not(_Strict):
    op: Literal["not"]
    of: Predicate


Predicate = Annotated[
    Always | Present | Equals | In | CatalogContains | All | AnyOf | Not,
    Field(discriminator="op"),
]
All.model_rebuild()
AnyOf.model_rebuild()
Not.model_rebuild()


def _nodes(predicate: Any, depth: int = 1) -> tuple[int, int]:
    """(node count, depth) of a predicate tree."""
    children = (
        predicate.of
        if isinstance(predicate, All | AnyOf)
        else [predicate.of]
        if isinstance(predicate, Not)
        else []
    )
    count, deepest = 1, depth
    for child in children:
        nodes, level = _nodes(child, depth + 1)
        count += nodes
        deepest = max(deepest, level)
    return count, deepest


def evaluate(predicate: Any, answers: dict[str, Any]) -> bool:
    """Pure, bounded evaluation over accepted answers (bounds enforced at build time)."""
    if isinstance(predicate, Always):
        return True
    if isinstance(predicate, Present | CatalogContains):
        value = answers.get(predicate.field)
        return value is not None and value != "" and value != []
    if isinstance(predicate, Equals):
        return answers.get(predicate.field) == predicate.value
    if isinstance(predicate, In):
        return answers.get(predicate.field) in predicate.values
    if isinstance(predicate, All):
        return all(evaluate(item, answers) for item in predicate.of)
    if isinstance(predicate, AnyOf):
        return any(evaluate(item, answers) for item in predicate.of)
    if isinstance(predicate, Not):
        return not evaluate(predicate.of, answers)
    raise TypeError("Unsupported predicate")


# --- Fields and stages -----------------------------------------------------------------------


class FieldSpec(_Strict):
    """One typed answer the interview collects and where it lands in the artifact."""

    key: Slug
    title: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    type: FieldType
    # JSON pointer into the artifact's ``creation`` object; None = interview-only context.
    target: Annotated[str, StringConstraints(pattern=r"^/creation/[a-z_]{1,64}$")] | None = None
    min_length: Annotated[int, Field(ge=0, le=100_000)] | None = None
    max_length: Annotated[int, Field(ge=1, le=100_000)] | None = None
    pattern: Annotated[str, StringConstraints(max_length=200)] | None = None
    options: Annotated[list[StrictStr], Field(min_length=1, max_length=64)] | None = None
    max_items: Annotated[int, Field(ge=1, le=100)] | None = None
    catalog: CatalogKind | None = None
    # How a catalog_ref_list maps into the artifact: plain keys or Tool references.
    shape: Literal["keys", "tool_refs"] = "keys"

    @model_validator(mode="after")
    def _typed(self) -> Self:
        catalogued = self.type in ("catalog_ref", "catalog_ref_list")
        if catalogued != (self.catalog is not None):
            raise ValueError("Only catalog fields name a catalog kind")
        if (self.type == "enum") != (self.options is not None):
            raise ValueError("Only enum fields declare options")
        if self.pattern is not None:
            if self.type not in ("text", "text_list"):
                raise ValueError("Only text fields declare a pattern")
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError("Invalid field pattern") from exc
        if self.shape == "tool_refs" and not (
            self.type == "catalog_ref_list" and self.catalog == "tool"
        ):
            raise ValueError("Tool references come only from a Tool catalog list")
        if (
            self.min_length is not None
            and self.max_length is not None
            and self.min_length > self.max_length
        ):
            raise ValueError("min_length exceeds max_length")
        return self


class Transition(_Strict):
    to: Slug
    when: Predicate


class Stage(_Strict):
    key: Slug
    title: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    collects: Annotated[list[Slug], Field(max_length=MAX_FIELDS)]
    required: Annotated[list[Slug], Field(max_length=MAX_FIELDS)] = Field(default_factory=list)
    # Defaults to "every required field present" when omitted.
    completion: Predicate | None = None
    transitions: Annotated[list[Transition], Field(max_length=16)] = Field(default_factory=list)
    # Anchor of a guidance section (expression material only).
    guidance: Slug | None = None


class ArtifactContract(_Strict):
    contract: Literal["caos.agent_creation_draft"]
    schema_version: Literal[1]
    resource_type: Literal["agent"]


class CapabilityRef(_Strict):
    """A declared, never-invoked dependency (e.g. the future proposal handoff)."""

    signal: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.]{0,127}$")]
    version: SemVer
    purpose: Annotated[str, StringConstraints(min_length=1, max_length=200)]


class ManuscriptDefinition(_Strict):
    contract: Literal["caos.manuscript"]
    schema_version: Literal[1]
    id: Slug
    version: SemVer
    title: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    description: Annotated[str, StringConstraints(max_length=2000)] = ""
    artifact: ArtifactContract
    # Fields a session may be started with (e.g. from the human's current draft).
    prefill: Annotated[list[Slug], Field(max_length=MAX_FIELDS)] = Field(default_factory=list)
    fields: Annotated[list[FieldSpec], Field(min_length=1, max_length=MAX_FIELDS)]
    stages: Annotated[list[Stage], Field(min_length=1, max_length=MAX_STAGES)]
    initial_stage: Slug
    final_stage: Slug
    completion: Predicate
    catalog_dependencies: list[CatalogKind] = Field(default_factory=list)
    handoff: CapabilityRef | None = None
    guidance: Annotated[str, StringConstraints(max_length=50_000)] = ""

    @model_validator(mode="after")
    def _well_formed(self) -> Self:
        fields = {field.key: field for field in self.fields}
        stages = {stage.key: stage for stage in self.stages}
        if len(fields) != len(self.fields) or len(stages) != len(self.stages):
            raise ValueError("Field and stage keys are unique")
        targets = [field.target for field in self.fields if field.target]
        if len(set(targets)) != len(targets):
            raise ValueError("Each artifact target has one field")
        owners: dict[str, str] = {}
        for stage in self.stages:
            for key in stage.collects:
                if key not in fields:
                    raise ValueError(f"Stage {stage.key} collects unknown field {key}")
                if key in owners:
                    raise ValueError(f"Field {key} is collected by more than one stage")
                owners[key] = stage.key
            if not set(stage.required) <= set(stage.collects):
                raise ValueError(f"Stage {stage.key} requires fields it does not collect")
            if stage.collects and not stage.required and stage.completion is None:
                # It would complete before its question is asked: require an explicit
                # answer or an explicit completion predicate.
                raise ValueError(f"Stage {stage.key} would complete without any input")
            for transition in stage.transitions:
                if transition.to not in stages:
                    raise ValueError(f"Unknown transition target {transition.to}")
        if set(owners) != set(fields):
            raise ValueError("Every field is collected by exactly one stage")
        if self.initial_stage not in stages or self.final_stage not in stages:
            raise ValueError("Initial and final stages must exist")
        if stages[self.final_stage].transitions:
            raise ValueError("The final stage has no transitions")
        if not set(self.prefill) <= set(fields):
            raise ValueError("Only declared fields can be prefilled")
        if self.handoff is not None and (
            (self.handoff.signal, self.handoff.version) not in ALLOWED_HANDOFFS
        ):
            raise ValueError("Unsupported Manuscript handoff")
        catalogs = {field.catalog for field in self.fields if field.catalog}
        if catalogs != set(self.catalog_dependencies):
            raise ValueError("Catalog dependencies must be exactly the catalogs fields use")
        reachable, frontier = {self.initial_stage}, [self.initial_stage]
        while frontier:
            for transition in stages[frontier.pop()].transitions:
                if transition.to not in reachable:
                    reachable.add(transition.to)
                    frontier.append(transition.to)
        if reachable != set(stages):
            raise ValueError("Every stage is reachable from the initial stage")
        predicates = [self.completion] + [
            item
            for stage in self.stages
            for item in [stage.completion, *(t.when for t in stage.transitions)]
            if item is not None
        ]
        for predicate in predicates:
            self._check(predicate, fields)
        return self

    @staticmethod
    def _check(predicate: Any, fields: dict[str, FieldSpec]) -> None:
        """Type-check a predicate tree against the declared fields, within bounds."""
        nodes, depth = _nodes(predicate)
        if nodes > MAX_PREDICATE_NODES or depth > MAX_PREDICATE_DEPTH:
            raise ValueError("Predicate exceeds its evaluation bounds")

        def visit(node: Any) -> None:
            if isinstance(node, All | AnyOf):
                for child in node.of:
                    visit(child)
                return
            if isinstance(node, Not):
                visit(node.of)
                return
            if isinstance(node, Always):
                return
            spec = fields.get(node.field)
            if spec is None:
                raise ValueError(f"Predicate names unknown field {node.field}")
            if isinstance(node, CatalogContains) and spec.catalog is None:
                raise ValueError("catalog_contains applies only to catalog fields")
            if isinstance(node, Equals):
                expected = bool if spec.type == "boolean" else str
                if spec.type in ("text_list", "catalog_ref_list") or not isinstance(
                    node.value, expected
                ):
                    raise ValueError(f"equals value does not match field {spec.key}")
                if spec.options is not None and node.value not in spec.options:
                    raise ValueError(f"equals value is not an option of {spec.key}")
            if isinstance(node, In):
                if spec.type not in ("text", "enum", "catalog_ref"):
                    raise ValueError(f"in applies only to scalar text fields ({spec.key})")
                if spec.options is not None and not set(node.values) <= set(spec.options):
                    raise ValueError(f"in values are not options of {spec.key}")

        visit(predicate)

    def key(self) -> str:
        return f"{self.id}@{self.version}"

    def field(self, key: str) -> FieldSpec:
        return next(field for field in self.fields if field.key == key)

    def stage(self, key: str) -> Stage:
        return next(stage for stage in self.stages if stage.key == key)

    def owner(self, field: str) -> str:
        return next(stage.key for stage in self.stages if field in stage.collects)

    def fingerprint(self) -> str:
        """Deterministic: canonical machine definition plus the guidance digest."""
        machine = self.model_dump(mode="json", exclude={"guidance"})
        return sha256_hex({"definition": machine, "guidance": sha256_hex(self.guidance)})

    def outline(self) -> dict[str, Any]:
        """Client-safe catalog projection: identity, title and stage outline only."""
        return {
            "id": self.id,
            "version": self.version,
            "title": self.title,
            "fingerprint": self.fingerprint(),
            "stages": [{"key": stage.key, "title": stage.title} for stage in self.stages],
        }
