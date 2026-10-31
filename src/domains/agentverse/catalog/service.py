"""Deterministic, requester-aware Resource Catalog over the authoritative registries.

Delegates every decision it does not own:

* registry kinds (Agent Types, Roles, Souls, Tools, RulePacks): ``RegistryService``'s
  client projection (``list_for_client`` / ``get_info_for_client``), i.e. UTU's
  ``can_access_resource`` plus RG-02F nested-dependency checks and safe DTOs;
* Workflows: ``WorkflowService`` client read policy (``get_workflow`` / ``list_workflows``);
* Manuscripts: the Manuscript Registry's exact ``id@version`` resolution and the reviewed
  gateway visibility used by the Manuscript capability.

It stores nothing, writes nothing, constructs no Agent or Tool, holds no Store, Genesis,
Khepri, CPBX or permit handle and evaluates no execution authority. Presentation is built
only from already-projected client data, never from raw provider metadata.
"""

from __future__ import annotations

import builtins
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ValidationError

from src.base.entities.user import User
from src.base.operations.v2.admission import sha256_hex
from src.domains.agentverse.catalog.models import (
    REGISTRY_KINDS,
    AgentTypeDetails,
    AttachmentDecision,
    CatalogCandidate,
    CatalogDecisions,
    CatalogEntry,
    CatalogIdentity,
    CatalogKind,
    CatalogListRequest,
    CatalogListResult,
    CatalogResolution,
    CatalogResolveRequest,
    CatalogResourceRef,
    CompatibilityDecision,
    CompatibilityIssue,
    FallbackIcon,
    ManuscriptDetails,
    ManuscriptStage,
    RoleDetails,
    RulePackDetails,
    SoulDetails,
    ToolConfigField,
    ToolConfigOption,
    ToolConfiguration,
    ToolDetails,
    WorkflowDetails,
)
from src.domains.agentverse.entities.workflow import slugify_workflow_key
from src.domains.agentverse.exceptions import WorkflowNotFoundError
from src.domains.agentverse.registries.base import Registry, RegistryItem
from src.domains.agentverse.services.registry_service import RegistryService
from src.domains.agentverse.tools.validation_tool.agent_validator import (
    AgentDefinitionValidator,
    _rulepack_minimum,
)
from src.domains.agentverse.utils.canonicalize import canonicalize

if TYPE_CHECKING:
    from src.domains.agentverse.manuscripts.models import ManuscriptDefinition
    from src.domains.agentverse.manuscripts.registry import ManuscriptRegistry
    from src.domains.agentverse.services.workflow_service import WorkflowService


@dataclass(frozen=True)
class CatalogRequester:
    """Trusted, in-process requester context; never parsed from request data.

    ``user`` is the verified human whose visibility scopes every answer.
    ``source_agent_id`` is the Agent acting for them, when there is one (Manuscript
    visibility depends on it); it narrows visibility and never widens it.
    """

    user: User
    source_agent_id: str | None = None


ManuscriptVisibility = Callable[[CatalogRequester, "ManuscriptDefinition"], bool]


def gateway_manuscript_visibility(bindings: Any) -> ManuscriptVisibility:
    """The Manuscript capability's reviewed visibility, applied to a catalog requester."""
    from src.domains.agentverse.manuscripts.capability import gateway_visibility
    from src.domains.agentverse.manuscripts.service import ManuscriptActor

    visible = gateway_visibility(bindings)

    def check(requester: CatalogRequester, definition: ManuscriptDefinition) -> bool:
        user = requester.user
        if not requester.source_agent_id or not user.tenant_id or not user.id:
            return False
        actor = ManuscriptActor(
            tenant_id=user.tenant_id,
            subject_id=user.id,
            agent_id=requester.source_agent_id,
            kind="human",
            requester=user,
        )
        return visible(actor, definition)

    return check


def _deny_manuscripts(_: CatalogRequester, __: ManuscriptDefinition) -> bool:
    return False


@dataclass(frozen=True)
class _Visible:
    """A resource the requester may read: client projection plus raw composition data."""

    kind: CatalogKind
    key: str
    version: str
    projected: Mapping[str, Any]
    raw: Mapping[str, Any]
    name: str | None
    description: str | None
    fingerprint: str | None = None


def _requester_ok(requester: Any) -> bool:
    return (
        isinstance(requester, CatalogRequester)
        and isinstance(requester.user, User)
        and bool(requester.user.id and requester.user.id.strip())
    )


class ResourceCatalog:
    def __init__(
        self,
        *,
        registries: Mapping[str, Registry[Any]],
        workflow_service: WorkflowService,
        manuscripts: ManuscriptRegistry | None = None,
        manuscript_visible: ManuscriptVisibility | None = None,
    ) -> None:
        self._registries = registries
        self._projection = RegistryService(dict(registries))
        self._workflows = workflow_service
        self._manuscripts = manuscripts
        # No reviewed visibility binding means no Manuscript is visible.
        self._manuscript_visible = manuscript_visible or _deny_manuscripts

    # ------------------------------------------------------------------
    # Public contract
    # ------------------------------------------------------------------

    async def list(
        self, requester: CatalogRequester, request: CatalogListRequest
    ) -> CatalogListResult:
        if not _requester_ok(requester):
            return CatalogListResult(kind=request.kind, entries=())
        candidate = await self._candidate(requester, request.candidate)
        visible = sorted(await self._discover(requester, request.kind), key=lambda v: v.key)
        entries: builtins.list[CatalogEntry] = []
        next_after: str | None = None
        for item in visible:
            if request.after is not None and item.key <= request.after:
                continue
            entry = await self._entry(requester, item, candidate)
            if entry is None:
                continue
            if (
                request.compatible_only
                and entry.decisions.candidate_compatibility.status == "incompatible"
            ):
                continue
            if len(entries) == request.limit:
                next_after = entries[-1].ref.key
                break
            entries.append(entry)
        return CatalogListResult(kind=request.kind, entries=tuple(entries), next_after=next_after)

    async def resolve(
        self, requester: CatalogRequester, request: CatalogResolveRequest
    ) -> CatalogResolution:
        if not _requester_ok(requester):
            return CatalogResolution(status="unavailable")
        item = await self._lookup(requester, request.kind, request.value, request.version)
        if item is None:
            return CatalogResolution(status="unavailable")
        if request.version is not None and item.version != request.version:
            return CatalogResolution(status="version_unavailable")
        candidate = await self._candidate(requester, request.candidate)
        entry = await self._entry(requester, item, candidate)
        if entry is None:
            return CatalogResolution(status="unavailable")
        return CatalogResolution(status="resolved", entry=entry)

    async def resolve_ref(
        self, requester: CatalogRequester, kind: CatalogKind, value: str
    ) -> CatalogResourceRef | None:
        """The typed reference the requester may read, without presentation work."""
        if not _requester_ok(requester):
            return None
        item = await self._lookup(requester, kind, value, None)
        if item is None:
            return None
        return CatalogResourceRef(
            kind=kind, key=item.key, version=item.version, fingerprint=item.fingerprint
        )

    # ------------------------------------------------------------------
    # Discovery and lookup (delegated read policy)
    # ------------------------------------------------------------------

    async def _discover(
        self, requester: CatalogRequester, kind: CatalogKind
    ) -> builtins.list[_Visible]:
        user = requester.user
        if kind in REGISTRY_KINDS:
            registry_kind = REGISTRY_KINDS[kind]
            try:
                keys = self._projection.list_for_client(registry_kind, user)
            except KeyError:
                return []
            found = [self._registry_item(kind, key, user) for key in keys]
            return [item for item in found if item is not None]
        if kind == "workflow":
            docs = await self._workflows.list_workflows(user)
            found = [await self._workflow_item(doc.key, user) for doc in docs]
            return [item for item in found if item is not None]
        if kind == "manuscript":
            if self._manuscripts is None:
                return []
            found = [
                self._manuscript_item(requester, key) for key in sorted(self._manuscripts.keys())
            ]
            return [item for item in found if item is not None]
        return []

    async def _lookup(
        self, requester: CatalogRequester, kind: CatalogKind, value: str, version: str | None
    ) -> _Visible | None:
        user = requester.user
        if kind in REGISTRY_KINDS:
            registry = self._registries.get(REGISTRY_KINDS[kind])
            if registry is None:
                return None
            try:
                key = registry.resolve_key(value, kind=REGISTRY_KINDS[kind])
            except (KeyError, ValueError):
                return None
            return self._registry_item(kind, key, user)
        if kind == "workflow":
            return await self._workflow_item(slugify_workflow_key(value), user)
        if kind == "manuscript":
            if self._manuscripts is None:
                return None
            if "@" in value:
                return self._manuscript_item(requester, value)
            if version is None:
                return None  # Exact versions only; there is no "latest" Manuscript.
            exact = self._manuscript_item(requester, f"{value}@{version}")
            if exact is not None:
                return exact
            # Report a visible Manuscript of another version as version_unavailable.
            for key in sorted(self._manuscripts.keys()):
                if key.split("@", 1)[0] == value:
                    other = self._manuscript_item(requester, key)
                    if other is not None:
                        return other
            return None
        return None

    def _registry_item(self, kind: CatalogKind, key: str, user: User) -> _Visible | None:
        registry_kind = REGISTRY_KINDS[kind]
        try:
            projected = self._projection.get_info_for_client(registry_kind, key, user)
        except (KeyError, ValueError):
            return None
        raw = self._registries[registry_kind].get_info(key)
        if raw is None or projected.metadata.get("ownership") not in ("global", "tenant"):
            return None
        return _Visible(
            kind=kind,
            key=projected.key,
            version=projected.version,
            projected=projected.metadata,
            raw=raw.metadata or {},
            name=projected.name,
            description=projected.description,
        )

    async def _workflow_item(self, key: str, user: User) -> _Visible | None:
        if not key:
            return None
        try:
            doc = await self._workflows.get_workflow(key, user)
        except WorkflowNotFoundError:
            return None
        if doc.ownership not in ("global", "tenant"):
            return None
        found, _ = await self._workflows.get_workflows([doc.key])
        stored = found[0] if found else None
        if stored is None:  # Deleted between reads: fail closed.
            return None
        return _Visible(
            kind="workflow",
            key=doc.key,
            version=doc.version,
            projected={
                "ownership": doc.ownership,
                "tags": list(doc.tags),
                "ui": doc.ui,
                "input_count": len(doc.inputs),
            },
            raw={"required_tools": list(stored.required_tools)},
            name=doc.name,
            description=doc.description,
        )

    def _manuscript_item(self, requester: CatalogRequester, key: str) -> _Visible | None:
        from src.domains.agentverse.manuscripts.registry import ManuscriptUnavailable

        assert self._manuscripts is not None
        try:
            definition = self._manuscripts.resolve(key)
        except (ManuscriptUnavailable, KeyError):
            return None
        try:
            visible = bool(self._manuscript_visible(requester, definition))
        except Exception:  # noqa: BLE001 - a failing visibility check denies.
            visible = False
        if not visible:
            return None
        outline = definition.outline()
        return _Visible(
            kind="manuscript",
            key=definition.id,
            version=definition.version,
            projected={
                "ownership": "global",
                "stages": outline["stages"],
                "artifact_contract": definition.artifact.contract,
            },
            raw={},
            name=outline["title"],
            description=definition.description or None,
            fingerprint=outline["fingerprint"],
        )

    # ------------------------------------------------------------------
    # Candidate (advisory; only visible parts count)
    # ------------------------------------------------------------------

    async def _candidate(
        self, requester: CatalogRequester, candidate: CatalogCandidate | None
    ) -> _Candidate | None:
        if candidate is None:
            return None

        unavailable: set[str] = set()
        duplicated: set[str] = set()

        async def one(field: str, kind: CatalogKind, value: str | None) -> _Visible | None:
            if value is None:
                return None
            found = await self._lookup(requester, kind, value, None)
            if found is None:
                unavailable.add(field)
            return found

        async def many(
            field: str, kind: CatalogKind, values: tuple[str, ...]
        ) -> builtins.list[_Visible]:
            unique: dict[str, _Visible] = {}
            for value in values:
                found = await self._lookup(requester, kind, value, None)
                if found is None:
                    unavailable.add(field)
                elif found.key in unique:
                    duplicated.add(field)
                else:
                    unique[found.key] = found
            return list(unique.values())

        return _Candidate(
            agent_type=await one("type", "agent_type", candidate.type),
            role=await one("role_name", "role", candidate.role_name),
            soul=await one("personality_key", "soul", candidate.personality_key),
            tools=await many("tools", "tool", candidate.tools),
            workflows=await many("workflows", "workflow", candidate.workflows),
            rulepacks=await many("rule_pack_ids", "rulepack", candidate.rule_pack_ids),
            unavailable=frozenset(unavailable),
            duplicated=frozenset(duplicated),
        )

    # ------------------------------------------------------------------
    # Entry construction
    # ------------------------------------------------------------------

    async def _entry(
        self, requester: CatalogRequester, item: _Visible, candidate: _Candidate | None
    ) -> CatalogEntry | None:
        try:
            ref = CatalogResourceRef(
                kind=item.kind, key=item.key, version=item.version, fingerprint=item.fingerprint
            )
            identity = self._identity(item)
            details = self._details(requester, item)
            compatibility = await self._compatibility(requester, item, candidate)
            decisions = CatalogDecisions(
                candidate_compatibility=compatibility,
                candidate_attachment=self._attachment(item, candidate),
            )
            descriptor = {
                "ref": ref.model_dump(mode="json"),
                "identity": identity.model_dump(mode="json"),
                "details": details.model_dump(mode="json"),
            }
            return CatalogEntry(
                ref=ref,
                identity=identity,
                details=details,
                decisions=decisions,
                descriptor_digest=sha256_hex(descriptor),
            )
        except (ValidationError, TypeError, ValueError):
            return None  # Unprojectable metadata is omitted, never partially returned.

    @staticmethod
    def _identity(item: _Visible) -> CatalogIdentity:
        meta = item.projected
        ui = _mapping(meta.get("ui"))
        title = _text(meta.get("title")) or _text(item.name) or item.key
        summary = (
            _text(meta.get("summary")) or _text(item.description) or _text(meta.get("description"))
        )
        icon = _text(meta.get("icon")) or _text(ui.get("icon")) or _text(meta.get("emoji"))
        tags = tuple(str(tag)[:64] for tag in (meta.get("tags") or []) if isinstance(tag, str))[:16]
        return CatalogIdentity(
            title=title[:120],
            summary=summary[:500] if summary else None,
            icon=icon[:64] if icon else None,
            fallback_icon=fallback_icon(item.kind, item.key, title),
            category=(_text(ui.get("category")) or None),
            tags=tags,
            scope=meta["ownership"],
        )

    def _details(self, requester: CatalogRequester, item: _Visible) -> Any:
        meta = item.projected
        if item.kind == "agent_type":
            return AgentTypeDetails(
                agent_group=_text(meta.get("agent_group")),
                agent_subclass=_text(meta.get("agent_subclass")),
            )
        if item.kind == "role":
            caps = meta.get("capabilities") or {}
            return RoleDetails(
                agent_group=_text(meta.get("agent_group")),
                agent_subclass=_text(meta.get("agent_subclass")),
                required_tools=tuple(sorted(caps.get("required_tools") or {})),
                optional_tools=tuple(sorted(caps.get("optional_tools") or {})),
            )
        if item.kind == "soul":
            return SoulDetails()
        if item.kind == "tool":
            fields = _mapping(_mapping(_mapping(meta.get("ui")).get("admin")).get("fields"))
            return ToolDetails(
                tool_class=_text(meta.get("tool_class")),
                min_rulepacks=_rulepack_minimum(meta),
                configuration=ToolConfiguration(
                    configurable=bool(fields),
                    fields=tuple(
                        ToolConfigField(
                            key=key,
                            label=str(field.get("label") or key)[:120],
                            input=str(field.get("input") or "text")[:32],
                            required=bool(field.get("required")),
                            help=(_text(field.get("help")) or None),
                            options=_config_options(field.get("options")),
                        )
                        for key, field in sorted(fields.items())
                        if isinstance(field, Mapping)
                    ),
                    config_schema=dict(meta.get("agent_config_schema") or {}),
                ),
            )
        if item.kind == "workflow":
            return WorkflowDetails(input_count=int(meta.get("input_count") or 0))
        if item.kind == "rulepack":
            return RulePackDetails(
                domain=_text(meta.get("domain")), rule_count=len(meta.get("rules") or [])
            )
        return ManuscriptDetails(
            artifact_contract=str(meta["artifact_contract"]),
            stages=tuple(
                ManuscriptStage(key=stage["key"], title=stage["title"]) for stage in meta["stages"]
            ),
        )

    async def _compatibility(
        self, requester: CatalogRequester, item: _Visible, candidate: _Candidate | None
    ) -> CompatibilityDecision:
        if candidate is None:
            return CompatibilityDecision(status="not_evaluated")
        issues: builtins.list[CompatibilityIssue] = [
            # Unknown and hidden candidate references are the same finding, per field.
            *(
                CompatibilityIssue(
                    code="candidate.reference_unavailable", blocking=False, subject=field
                )
                for field in sorted(candidate.unavailable)
            ),
            *(
                CompatibilityIssue(
                    code="candidate.reference_duplicate", blocking=False, subject=field
                )
                for field in sorted(candidate.duplicated)
            ),
        ]
        unevaluated: builtins.list[Literal["agent_type", "role"]] = []
        user = requester.user
        if item.kind == "role":
            if candidate.agent_type is None:
                unevaluated.append("agent_type")
            else:
                issues += _type_role_mismatch(candidate.agent_type.raw, item.raw)
        if item.kind == "agent_type":
            if candidate.role is None:
                unevaluated.append("role")
            else:
                issues += _type_role_mismatch(item.raw, candidate.role.raw)
        if item.kind == "role":
            have = {tool.key for tool in candidate.tools}
            for key in sorted(AgentDefinitionValidator._role_required_tools(_as_item(item))):
                if self._registry_item("tool", key, user) is None:
                    issues.append(
                        CompatibilityIssue(
                            code="composition.required_tool_unavailable", blocking=True
                        )
                    )
                elif key not in have:
                    issues.append(
                        CompatibilityIssue(
                            code="composition.required_tool_missing", blocking=False, subject=key
                        )
                    )
        packs = len(candidate.rulepacks)
        if item.kind == "tool" and packs < _rulepack_minimum(item.raw):
            issues.append(
                CompatibilityIssue(code="composition.rulepacks_insufficient", blocking=False)
            )
        if item.kind == "workflow":
            tools = self._registries.get("tool")
            for key in sorted({canonicalize(t) for t in item.raw.get("required_tools", []) if t}):
                # Workflow tools are server-composed: registered, not necessarily readable.
                info = tools.get_info(key) if tools is not None else None
                if info is None:
                    issues.append(
                        CompatibilityIssue(
                            code="composition.workflow_tool_unavailable", blocking=True
                        )
                    )
                elif packs < _rulepack_minimum(info.metadata or {}):
                    issues.append(
                        CompatibilityIssue(
                            code="composition.rulepacks_insufficient", blocking=False
                        )
                    )
        # Identical findings collapse, so repetition never reveals a count.
        unique = list(dict.fromkeys(issues))
        status: Literal["compatible", "conditional", "requires_changes", "incompatible"]
        if any(issue.blocking for issue in unique):
            status = "incompatible"
        elif unique:
            status = "requires_changes"
        elif unevaluated:
            status = "conditional"
        else:
            status = "compatible"
        return CompatibilityDecision(
            status=status, issues=tuple(unique), unevaluated=tuple(unevaluated)
        )

    @staticmethod
    def _attachment(item: _Visible, candidate: _Candidate | None) -> AttachmentDecision:
        if candidate is None or item.kind == "manuscript":
            # Manuscripts are not attached to Agent definitions (a Tool config may name one).
            return AttachmentDecision(status="not_evaluated")
        attached = {
            "agent_type": {candidate.agent_type.key} if candidate.agent_type else set(),
            "role": {candidate.role.key} if candidate.role else set(),
            "soul": {candidate.soul.key} if candidate.soul else set(),
            "tool": {tool.key for tool in candidate.tools},
            "workflow": {workflow.key for workflow in candidate.workflows},
            "rulepack": {pack.key for pack in candidate.rulepacks},
        }[item.kind]
        return AttachmentDecision(status="attached" if item.key in attached else "not_attached")


@dataclass(frozen=True)
class _Candidate:
    agent_type: _Visible | None
    role: _Visible | None
    soul: _Visible | None
    tools: list[_Visible]
    workflows: list[_Visible]
    rulepacks: list[_Visible]
    # Candidate field names (never resource keys) whose references did not resolve.
    unavailable: frozenset[str] = frozenset()
    duplicated: frozenset[str] = frozenset()


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _config_options(raw: Any) -> tuple[ToolConfigOption, ...]:
    """Descriptive choices from the already-sanitized RG-02F field; string values only."""
    if not isinstance(raw, list):
        return ()
    return tuple(
        ToolConfigOption(
            value=option["value"][:128],
            label=str(option.get("label") or option["value"])[:120],
            description=(_text(option.get("description")) or "")[:500] or None,
        )
        for option in raw[:32]
        if isinstance(option, Mapping)
        and isinstance(option.get("value"), str)
        and option["value"]
        and not option.get("disabled")
    )


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _as_item(item: _Visible) -> RegistryItem:
    return RegistryItem(key=item.key, name=item.name or item.key, metadata=dict(item.raw))


def _type_role_mismatch(
    type_meta: Mapping[str, Any], role_meta: Mapping[str, Any]
) -> list[CompatibilityIssue]:
    """Same rule as ``data.validation``'s role/type composition check."""
    for field in ("agent_group", "agent_subclass"):
        declared = (type_meta.get(field), role_meta.get(field))
        if all(declared) and declared[0] != declared[1]:
            return [CompatibilityIssue(code="composition.role_type_mismatch", blocking=True)]
    return []


def fallback_icon(kind: CatalogKind, key: str, title: str) -> FallbackIcon:
    """Deterministic, media-free icon: kind glyph, up to two initials and a stable hue."""
    words = [w for w in "".join(c if c.isalnum() else " " for c in title).split() if w]
    text = "".join(w[0] for w in words[:2]).upper() or key[:1].upper() or "?"
    hue = int(sha256_hex(f"{kind}:{key}")[:8], 16) % 360
    return FallbackIcon(glyph=kind, text=text[:2], hue=hue)
