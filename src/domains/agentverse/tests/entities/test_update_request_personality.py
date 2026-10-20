"""Regression tests: a PATCH carries the personality reference key, and an
omitted/absent key means "leave it unchanged" and must be excluded from
`model_dump_for_update()`.
"""

from __future__ import annotations

from src.domains.agentverse.entities.agent_update_request import AgentUpdateRequest


def test_omitted_personality_key_is_not_in_update():
    req = AgentUpdateRequest(name="rename only")
    assert "personality_key" not in req.model_dump_for_update()


def test_null_personality_key_is_not_in_update():
    req = AgentUpdateRequest(name="x", personality_key=None)
    assert "personality_key" not in req.model_dump_for_update()


def test_real_personality_key_is_preserved_in_update():
    req = AgentUpdateRequest(name="x", personality_key="shakespeare")
    assert req.model_dump_for_update()["personality_key"] == "shakespeare"
