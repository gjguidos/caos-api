"""Activity contracts and resource resolvers (AGENT-CTX-0.1, ADR-0004).

Both registries use exact keys, reject conflicting registration and are frozen at
trusted bootstrap. A contract declares *typed slots* (resource kind, requiredness,
update rule, allowed states) and declarative cross-slot links; it never contains code
that touches resources. Resolvers are registered explicitly by the domain that owns a
resource kind and resolve a reference read-only for a verified actor through the owning
service. Registration grants nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

from src.base.operations.v2.admission import sha256_hex
from src.domains.agentverse.activities.models import ActivityContractUnavailable

if TYPE_CHECKING:
    from src.domains.agentverse.activities.service import ActivityActor

_KEY = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*@(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_KIND = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_SLOT = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
UpdateRule = Literal["immutable", "replace", "successor"]


@dataclass(frozen=True)
class ResourceLink:
    """A typed relation reported by a resolver (e.g. a session's attached draft)."""

    name: str
    kind: str
    id: str


@dataclass(frozen=True)
class ResolvedResource:
    """Read-only resolution of one reference: identity, revision and state only."""

    kind: str
    id: str
    revision: int
    status: str
    version: str | None = None
    fingerprint: str | None = None
    predecessor_id: str | None = None
    links: tuple[ResourceLink, ...] = ()

    def link(self, name: str) -> ResourceLink | None:
        return next((link for link in self.links if link.name == name), None)


class ResourceResolver(Protocol):
    """Owned by the resource's domain. Missing and not-the-actor's raise the same
    ``ActivityReferenceUnavailable``; storage outages raise ``ActivityUnavailable``."""

    kind: str

    async def resolve(self, actor: ActivityActor, resource_id: str) -> ResolvedResource: ...


@dataclass(frozen=True)
class ResourceSlot:
    name: str
    kind: str
    required: bool
    # immutable: never changes within a generation; replace: any resolvable reference;
    # successor: only a resource whose predecessor is the current one, same version and
    # fingerprint (e.g. a Manuscript restart successor).
    update: UpdateRule
    start_statuses: frozenset[str]
    update_statuses: frozenset[str]


@dataclass(frozen=True)
class SlotLink:
    """``resolved[source].link(name).id`` must equal ``resolved[target].id``."""

    source: str
    name: str
    target: str


@dataclass(frozen=True)
class ActivityContract:
    """A registered, versioned activity (``caos.activity.contract`` v1)."""

    id: str
    version: str
    title: str
    description: str
    owner_kinds: frozenset[str]
    slots: tuple[ResourceSlot, ...]
    links: tuple[SlotLink, ...] = field(default=())

    def key(self) -> str:
        return f"{self.id}@{self.version}"

    def slot(self, name: str) -> ResourceSlot | None:
        return next((slot for slot in self.slots if slot.name == name), None)

    def fingerprint(self) -> str:
        return sha256_hex(
            {
                "contract": "caos.activity.contract",
                "schema_version": 1,
                "key": self.key(),
                "title": self.title,
                "owner_kinds": sorted(self.owner_kinds),
                "slots": [
                    {
                        "name": s.name,
                        "kind": s.kind,
                        "required": s.required,
                        "update": s.update,
                        "start_statuses": sorted(s.start_statuses),
                        "update_statuses": sorted(s.update_statuses),
                    }
                    for s in self.slots
                ],
                "links": [[x.source, x.name, x.target] for x in self.links],
            }
        )


class RegistryFrozen(RuntimeError):
    pass


class ResourceResolverRegistry:
    def __init__(self) -> None:
        self._resolvers: dict[str, ResourceResolver] = {}
        self.frozen = False

    def register(self, resolver: ResourceResolver) -> None:
        if self.frozen:
            raise RegistryFrozen("Resource resolver registry is frozen")
        kind = resolver.kind
        if not _KIND.fullmatch(kind):
            raise ValueError(f"Invalid resource kind: {kind!r}")
        existing = self._resolvers.get(kind)
        if existing is not None and existing is not resolver:
            raise ValueError(f"Conflicting resolver for {kind}")
        self._resolvers[kind] = resolver

    def get(self, kind: str) -> ResourceResolver:
        try:
            return self._resolvers[kind]
        except KeyError as exc:
            raise LookupError(f"No resolver for {kind}") from exc

    def kinds(self) -> frozenset[str]:
        return frozenset(self._resolvers)

    def freeze(self) -> None:
        self.frozen = True


class ActivityContractRegistry:
    """Exact ``id@version`` contracts; validated against registered resolver kinds."""

    def __init__(self, resolvers: ResourceResolverRegistry, owner_kinds: frozenset[str]) -> None:
        self._contracts: dict[str, ActivityContract] = {}
        self._resolvers = resolvers
        self._owner_kinds = owner_kinds
        self.frozen = False

    def register(self, contract: ActivityContract) -> None:
        if self.frozen:
            raise RegistryFrozen("Activity contract registry is frozen")
        key = contract.key()
        if not _KEY.fullmatch(key) or not contract.title:
            raise ValueError(f"Invalid activity contract: {key!r}")
        if not contract.owner_kinds or not contract.owner_kinds <= self._owner_kinds:
            raise ValueError(f"{key}: unknown owner kind")
        names = [slot.name for slot in contract.slots]
        if not names or len(set(names)) != len(names) or len(names) > 8:
            raise ValueError(f"{key}: slots must be 1-8 unique names")
        for slot in contract.slots:
            if not _SLOT.fullmatch(slot.name) or slot.kind not in self._resolvers.kinds():
                raise ValueError(f"{key}: slot {slot.name!r} has no registered resolver")
            if not slot.start_statuses or not slot.update_statuses:
                raise ValueError(f"{key}: slot {slot.name!r} must name its allowed states")
        for link in contract.links:
            if contract.slot(link.source) is None or contract.slot(link.target) is None:
                raise ValueError(f"{key}: link between unknown slots")
        existing = self._contracts.get(key)
        if existing is not None and existing.fingerprint() != contract.fingerprint():
            raise ValueError(f"Conflicting activity contract {key}")
        self._contracts[key] = contract

    def resolve(self, key: str, fingerprint: str | None = None) -> ActivityContract:
        contract = self._contracts.get(key)
        if contract is None or (fingerprint is not None and contract.fingerprint() != fingerprint):
            raise ActivityContractUnavailable("Activity contract is not registered")
        return contract

    def keys(self) -> list[str]:
        return sorted(self._contracts)

    def freeze(self) -> None:
        self.frozen = True
