"""``CollectionDocument.embedding_model`` records the embedding backend a collection's
vectors were written with. None means "the global default", which is what every
collection created before the field was stamped actually used.
"""

from __future__ import annotations

from src.domains.agentverse.entities.collection import CollectionDocument


def _doc(**overrides):
    base = {"name": "handbook", "agent_id": "agent-1", "owner_id": "user-1"}
    return {**base, **overrides}


def test_unstamped_collections_default_to_none():
    col = CollectionDocument(**_doc())

    assert col.embedding_model is None


def test_legacy_placeholder_is_normalised_to_none():
    """Stored documents carry a placeholder that never named a real backend."""
    col = CollectionDocument(**_doc(embedding_model="openai-embedding-v2"))

    assert col.embedding_model is None


def test_stamped_embedding_model_round_trips():
    col = CollectionDocument(**_doc(embedding_model="text-embedding-ada-002-stig"))

    assert col.embedding_model == "text-embedding-ada-002-stig"
    assert col.model_dump()["embedding_model"] == "text-embedding-ada-002-stig"
