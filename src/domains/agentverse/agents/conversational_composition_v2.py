"""Registry-driven V2 conversation composition (CONVERSATION-0.1A, ADR-0004).

Replaces the hardcoded Agent Type/Role gate. An Agent may converse in a mode only if:

1. its V2 definition explicitly attaches the published ``conversation@1.0.0`` Tool with a
   valid configuration allowing that mode (and declaring only registered, exact activity
   contracts for the ``conversation.v2`` owner), or
2. it has no attachment and is one of the reviewed, temporary legacy definitions: an
   exact, immutable ``(tenant, agent ID)`` identity whose ``(type, role_name)`` still
   matches (AGENT-DEF-SEC-0.1). A new Agent never qualifies by its Type/Role; and
3. independently, the Agent holds the existing ``chat.context`` / ``chat.response`` UTU
   grant, checked by the caller on every turn.

Composition is availability, never authority: nothing here reads or grants capabilities.
No branch depends on a particular Agent's identity; the compatibility table is data.
An explicit attachment always wins: an invalid one never falls back to compatibility.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

from pydantic import ValidationError

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.registries import tool_registry_instance
from src.domains.agentverse.registries.tool_registry import ToolRegistry
from src.domains.agentverse.tools.conversation_tool import ConversationTool  # noqa: F401
from src.domains.agentverse.tools.conversation_tool.spec import (
    ALL_MODES,
    TOOL_KEY,
    TOOL_VERSION,
    ConversationMode,
    ConversationToolConfig,
)
from src.domains.agentverse.tools.spec import AgentToolRef

COMPOSITION_UNAVAILABLE: Final = "composition_unavailable"
Source = Literal["tool", "legacy", "none"]
# A predicate over exact activity keys usable by a ``conversation.v2`` owner, or ``None``
# for format-only checks (presentation, diagnostics, creation validation).
ActivityCheck = Callable[[str], bool] | None


class ConversationUnavailable(RuntimeError):
    """The Agent's composition does not allow this conversation. Requester-facing code is
    always ``composition_unavailable``; ``reason`` is internal diagnostics only."""

    code = COMPOSITION_UNAVAILABLE

    def __init__(self, reason: str) -> None:
        super().__init__(COMPOSITION_UNAVAILABLE)
        self.reason = reason


@dataclass(frozen=True)
class LegacyConversationEntry:
    """A reviewed, temporary compatibility declaration for one existing definition.

    Bound to an immutable identity (tenant and Agent ID, which no route can change), and
    valid only while the definition still has the reviewed ``(type, role_name)``. It stands
    in for an explicit ``conversation@1.0.0`` attachment with the default configuration
    restricted to ``modes`` and exactly the declared ``activities`` (MIG-FIX-0.1: none by
    default, so a contract registered later is never implied). It grants nothing: existing
    UTU grants still decide every turn and every activity.
    """

    id: str
    tenant_id: str
    agent_id: str
    agent_type: str
    role_name: str
    modes: tuple[ConversationMode, ...] = ALL_MODES
    activities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # Validated like an explicit attachment: exact keys, no duplicates, bounded.
        self.config()

    def config(self) -> ConversationToolConfig:
        return ConversationToolConfig(modes=list(self.modes), activities=list(self.activities))


# The allowlist: exactly the conversational definitions that existed before
# CONVERSATION-0.1A (Handoff §3). Enki and Thoth are the policy-pinned Genesis identities
# (``dependencies/genesis_local.py``); Nabu is the one manually created chat/general Agent.
# Retire each entry once governed definition revision attaches ``conversation@1.0.0`` to it
# (docs/CONVERSATION_TOOL.md §5). Do not add entries without architectural review.
LEGACY_CONVERSATION_COMPATIBILITY: Final[tuple[LegacyConversationEntry, ...]] = (
    LegacyConversationEntry(
        "legacy.v2.enki",
        "caos-local",
        "00000000-0000-4000-8000-000000000004",
        "form",
        "enki",
        # Enki's existing creation interview; Thoth and Nabu declare none.
        activities=("agent.creation_interview@1.0.0",),
    ),
    LegacyConversationEntry(
        "legacy.v2.thoth",
        "caos-local",
        "00000000-0000-4000-8000-000000000006",
        "chat",
        "thoth",
    ),
    LegacyConversationEntry(
        "legacy.v2.nabu",
        "caos-local",
        "697e8c4b-171a-b672-c90e-f4b6ca5dd05b",
        "chat",
        "general",
    ),
)


@dataclass(frozen=True)
class ConversationComposition:
    source: Source
    config: ConversationToolConfig | None = None
    compatibility: str | None = None
    reason: str | None = None

    @property
    def available(self) -> bool:
        return self.config is not None

    @property
    def modes(self) -> frozenset[str]:
        return frozenset(self.config.modes) if self.config else frozenset()

    def allows(self, mode: str) -> bool:
        return mode in self.modes

    def require(self, mode: str) -> ConversationToolConfig:
        """The effective configuration if ``mode`` is composed; grants are separate."""
        if self.config is None:
            raise ConversationUnavailable(self.reason or "unavailable")
        if mode not in self.config.modes:
            raise ConversationUnavailable("mode_not_allowed")
        return self.config


def _unavailable(reason: str, source: Source = "none") -> ConversationComposition:
    return ConversationComposition(source=source, reason=reason)


def legacy_entry(
    definition: Mapping[str, Any],
    compatibility: tuple[LegacyConversationEntry, ...] = LEGACY_CONVERSATION_COMPATIBILITY,
) -> LegacyConversationEntry | None:
    """The reviewed entry for this exact definition identity, if its shape still matches.
    Type/Role alone never match: the identity must be on the allowlist."""
    # A missing role is the runtime's effective ``general`` role (CognitiveResponseV2).
    key = (
        definition.get("tenant_id"),
        definition.get("id"),
        definition.get("type"),
        definition.get("role_name") or "general",
    )
    return next(
        (e for e in compatibility if (e.tenant_id, e.agent_id, e.agent_type, e.role_name) == key),
        None,
    )


def conversation_attachments(definition: Mapping[str, Any]) -> list[AgentToolRef] | None:
    """The definition's ``conversation`` attachments; ``None`` if ``tools`` is malformed."""
    tools = definition.get("tools") or []
    if not isinstance(tools, list):
        return None
    found = []
    for item in tools:
        try:
            ref = AgentToolRef.model_validate(item)
        except (ValidationError, TypeError):
            return None
        if ref.key == TOOL_KEY:
            found.append(ref)
    return found


def resolve_conversation(
    definition: Mapping[str, Any] | None,
    *,
    tools: ToolRegistry = tool_registry_instance,
    activities: ActivityCheck = None,
    compatibility: tuple[LegacyConversationEntry, ...] = LEGACY_CONVERSATION_COMPATIBILITY,
) -> ConversationComposition:
    """Deterministic: the same definition, Tool registry and activity registry always
    resolve the same way."""
    if not isinstance(definition, Mapping) or definition.get("runtime_version") != "v2":
        return _unavailable("runtime_unsupported")
    attached = conversation_attachments(definition)
    if attached is None:
        return _unavailable("definition_invalid")
    if len(attached) > 1:
        return _unavailable("attachment_duplicate", "tool")
    if attached:
        item = tools.get_info(TOOL_KEY)
        if item is None or item.version != TOOL_VERSION:
            return _unavailable("tool_unpublished", "tool")
        try:
            config = ConversationToolConfig.model_validate(attached[0].config)
        except ValidationError:
            return _unavailable("config_invalid", "tool")
        if activities is not None and not all(activities(key) for key in config.activities):
            return _unavailable("activity_unavailable", "tool")
        return ConversationComposition(source="tool", config=config)
    entry = legacy_entry(definition, compatibility)
    if entry is None:
        return _unavailable("not_attached")
    # Activities are not checked here: an unavailable contract must not change the entry's
    # conversation modes. Consent validates them (``ConversationGate.declared_activities``).
    return ConversationComposition(source="legacy", config=entry.config(), compatibility=entry.id)


class ConversationGate:
    """Runtime resolver over trusted persisted definitions (dispatcher, ``chat.response``).

    Activity contracts are checked against the frozen Activity Contract Registry bound at
    trusted startup; until bound, any declared activity fails closed. The Activity
    Context service does not depend on this gate or on the Conversation Tool.
    """

    def __init__(
        self,
        *,
        definitions: Any,
        tools: ToolRegistry = tool_registry_instance,
        compatibility: tuple[LegacyConversationEntry, ...] = LEGACY_CONVERSATION_COMPATIBILITY,
    ) -> None:
        self._definitions = definitions
        self._tools = tools
        self._compatibility = compatibility
        self._activities: Any = None

    def bind_activities(self, contracts: Any) -> None:
        if not getattr(contracts, "frozen", False):
            raise ValueError("Activity contracts must be frozen before conversation binding")
        if self._activities is not None and self._activities is not contracts:
            raise ValueError("Conversation activity contracts are already bound")
        self._activities = contracts

    def _activity(self, key: str) -> bool:
        from src.domains.agentverse.activities.models import ActivityContractUnavailable
        from src.domains.agentverse.activities.owners import CONVERSATION_OWNER

        if self._activities is None:
            return False
        try:
            contract = self._activities.resolve(key)
        except ActivityContractUnavailable:
            return False
        return CONVERSATION_OWNER in contract.owner_kinds

    def resolve(self, definition: Mapping[str, Any] | None) -> ConversationComposition:
        return resolve_conversation(
            definition,
            tools=self._tools,
            activities=self._activity,
            compatibility=self._compatibility,
        )

    async def _definition(self, tenant_id: str, agent_id: str) -> Mapping[str, Any]:
        definition = await self._definitions.find_by_id(agent_id)
        # Same denial as the V2 runtime for a missing, V1 or other-tenant definition.
        if (
            not definition
            or definition.get("runtime_version") != "v2"
            or definition.get("tenant_id", tenant_id) != tenant_id
        ):
            raise EventPermissionError("Explicit V2 definition required")
        return definition

    async def require(self, tenant_id: str, agent_id: str, mode: str) -> ConversationToolConfig:
        return (await self.admit(tenant_id, agent_id, mode))[0]

    async def admit(
        self, tenant_id: str, agent_id: str, mode: str
    ) -> tuple[ConversationToolConfig, DefinitionBinding]:
        """REV-5 admission: composition and the definition binding from ONE read, so the
        Tool attachments and configuration of a turn come from one definition."""
        definition = await self._definition(tenant_id, agent_id)
        config = self.resolve(definition).require(mode)
        return config, bind_definition(tenant_id, agent_id, definition)

    async def ensure_current(
        self, tenant_id: str, agent_id: str, binding: DefinitionBinding, mode: str
    ) -> None:
        """Before consequential execution: the definition is still the admitted one and
        still composes this mode. Never authority: UTU grants are checked separately."""
        definition = await self._definition(tenant_id, agent_id)
        if bind_definition(tenant_id, agent_id, definition) != binding:
            raise DefinitionChanged(agent_id)
        self.resolve(definition).require(mode)

    async def declared_activities(self, tenant_id: str, agent_id: str) -> frozenset[str]:
        """Activity contracts the Agent's current composition declares (REV-5, MIG-FIX-0.1).

        Always an explicit set: an attached Conversation Tool's ``activities`` or a legacy
        entry's reviewed ``activities``, each kept only while it resolves in the bound
        Activity Contract Registry for a ``conversation.v2`` owner. No composition, an
        invalid one, or unbound contracts declare nothing; nothing is unrestricted.
        """
        definition = await self._definition(tenant_id, agent_id)
        composition = self.resolve(definition)
        if composition.config is None:
            return frozenset()
        return frozenset(key for key in composition.config.activities if self._activity(key))


@dataclass(frozen=True)
class DefinitionBinding:
    """The authoritative definition a turn was admitted under (REV-5).

    ``definition_digest`` is the canonical ``caos.agent_definition@1`` digest of the
    projection; ``revision`` / ``revision_digest`` name the applied governed revision, or
    ``None`` for a definition never revised. A binding never confers authority.
    """

    agent_id: str
    definition_digest: str
    revision: int | None = None
    revision_digest: str | None = None

    def wire(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "revision": self.revision,
            "revision_digest": self.revision_digest,
            "definition_digest": self.definition_digest,
        }


class DefinitionChanged(RuntimeError):
    """The definition changed after admission; the turn did not execute. Retryable as a
    **new** turn (new event ID): the journaled outcome of this turn is never replayed
    into execution."""

    code = "definition_changed"
    retryable = True

    def __init__(self, agent_id: str) -> None:
        super().__init__("definition_changed")
        self.agent_id = agent_id


def bind_definition(
    tenant_id: str, agent_id: str, definition: Mapping[str, Any]
) -> DefinitionBinding:
    import json

    from src.base.operations.v2.admission import sha256_hex
    from src.domains.agentverse.agents.definition_revisions.canonical import (
        MARKER_FIELDS,
        STORAGE_FIELDS,
        CanonicalizationError,
        canonical_definition,
        definition_digest,
    )

    try:
        digest = definition_digest(tenant_id, agent_id, canonical_definition(definition))
    except CanonicalizationError:
        # Not a canonical definition (e.g. a partial legacy record): still bind it exactly,
        # as a change detector over its stored fields. Composition decides availability.
        raw = {k: v for k, v in definition.items() if k not in STORAGE_FIELDS | MARKER_FIELDS}
        digest = sha256_hex(
            {
                "contract": "caos.agent_definition.raw",
                "tenant_id": tenant_id,
                "agent_id": agent_id,
                "definition": json.loads(json.dumps(raw, default=str)),
            }
        )
    marker = definition.get("definition_revision")
    marker = marker if isinstance(marker, Mapping) else {}
    revision = marker.get("revision")
    return DefinitionBinding(
        agent_id=agent_id,
        definition_digest=digest,
        revision=revision if isinstance(revision, int) else None,
        revision_digest=marker.get("revision_digest") if isinstance(revision, int) else None,
    )
