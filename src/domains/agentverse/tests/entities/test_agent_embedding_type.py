"""Regression tests: the agent's embedding model is part of its persisted
cognitive modules, and documents stored before the field existed still load.
"""

from __future__ import annotations

from typing import Any

from src.domains.agentverse.entities.agent import Agent


def _doc(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": "agent-1",
        "name": "Min agent",
        "system_name": "min_agent",
        "type": "chat",
        "prompt": "a prompt",
        "chat_url": "/chat/agent-1",
        "llm_type": "gpt-6-astra",
        "audio_type": "gpt-audio-1.5",
        "db_type": "mongodb",
        "cache_type": "redis",
        "knowledge_db_type": "chromadb",
        "messaging_type": "",
        "dna_sequence": "deadbeef",
    }
    return {**base, **overrides}


def test_legacy_document_without_embedding_type_loads():
    agent = Agent(**_doc())

    assert agent.embedding_type is None


def test_embedding_type_round_trips():
    agent = Agent(**_doc(embedding_type="text-embedding-ada-002-stig"))

    assert agent.embedding_type == "text-embedding-ada-002-stig"
    assert agent.model_dump()["embedding_type"] == "text-embedding-ada-002-stig"
