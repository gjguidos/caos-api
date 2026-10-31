"""Canonical Agent definitions (REV-1).

``caos.agent_definition@1`` is the complete authoritative definition of one Agent: every
``Agent`` model field except the runtime-only ``commbridge``, as the model serializes it
in JSON mode, with storage metadata (``_id``, timestamps, the ``store`` creation record)
excluded. The field set is pinned: any ``Agent`` model change must introduce a new
canonical schema version rather than silently changing what a v1 digest means.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from pydantic import ValidationError

from src.base.operations.v2.admission import sha256_hex
from src.domains.agentverse.entities.agent import Agent

CANONICAL_SCHEMA: Final = "caos.agent_definition@1"
# Exactly the authoritative fields of canonical schema v1 (pinned; tested against Agent).
CANONICAL_V1_FIELDS: Final = frozenset(
    {
        "exposure",
        "ownership",
        "tenant_id",
        "runtime_version",
        "id",
        "creator",
        "name",
        "system_name",
        "type",
        "role_name",
        "personality_key",
        "prompt",
        "chat_url",
        "llm_type",
        "audio_type",
        "image_type",
        "video_type",
        "db_type",
        "cache_type",
        "knowledge_db_type",
        "embedding_type",
        "collections",
        "messaging_type",
        "dna_sequence",
        "tools",
        "workflows",
        "rule_pack_ids",
        "visibility",
        "whitelisted_groups",
        "gui_components",
    }
)
# Runtime objects that are never part of a definition.
RUNTIME_FIELDS: Final = frozenset({"commbridge"})
# Storage metadata written around a definition (Store, DBService, MongoDB).
STORAGE_FIELDS: Final = frozenset(
    {"_id", "created_at", "updated_at", "created", "modified", "store"}
)
# Server-managed provenance markers outside the Agent model (seeds; future projection).
MARKER_FIELDS: Final = frozenset({"genesis_local_seed", "tenant_scoped", "definition_revision"})


class CanonicalizationError(ValueError):
    """The document is not a valid definition under the current ``Agent`` model."""


def unknown_fields(document: Mapping[str, Any]) -> frozenset[str]:
    """Persisted fields that are neither definition, runtime, storage nor marker fields."""
    known = set(Agent.model_fields) | STORAGE_FIELDS | MARKER_FIELDS
    return frozenset(document) - known


def agent_model_dump(document: Mapping[str, Any]) -> dict[str, Any]:
    """The persisted definition as the current ``Agent`` model serializes it (JSON mode).

    This is how ``store.create`` computed its historical request digest. Unknown fields
    are dropped by the model, so callers must check ``unknown_fields`` themselves.
    """
    payload = {
        key: value
        for key, value in document.items()
        if key not in STORAGE_FIELDS and key not in MARKER_FIELDS
    }
    try:
        return Agent.model_validate(payload).model_dump(mode="json")
    except ValidationError as exc:
        raise CanonicalizationError("Definition is invalid under the current model") from exc


def canonical_definition(document: Mapping[str, Any]) -> dict[str, Any]:
    """``caos.agent_definition@1`` for a persisted document."""
    dumped = agent_model_dump(document)
    return {key: dumped[key] for key in sorted(CANONICAL_V1_FIELDS)}


def definition_digest(tenant_id: str, agent_id: str, definition: Mapping[str, Any]) -> str:
    return sha256_hex(
        {
            "contract": CANONICAL_SCHEMA,
            "tenant_id": tenant_id,
            "agent_id": agent_id,
            "definition": dict(definition),
        }
    )
