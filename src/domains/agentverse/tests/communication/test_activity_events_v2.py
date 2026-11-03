"""COORD-0.1 client event contract and deterministic prompt (no infrastructure)."""

import hashlib

import pytest
from pydantic import ValidationError

from src.domains.agentverse.entities.conversation_v2 import (
    ActivityCloseV2,
    ActivityStartV2,
    ClientEventV2,
)
from src.domains.agentverse.manuscripts.definitions import AGENT_CREATION_INTERVIEW
from src.domains.agentverse.services.creation_interview_coordinator_v2 import prompt

BASE = {"conversation_id": "c-1", "event_id": "e-1", "agent_ids": ["enki"]}
START = {"key": "agent.creation_interview@1.0.0", "references": {"draft": "d-1"}}
CLOSE = {"context_id": "ctx-1", "reason": "abandoned"}


def journal(event):
    """The turn journal's request bytes (``ConversationRepositoryV2.begin``)."""
    exclude = {"response_mode"} if event.response_mode == "context" else set()
    return event.model_dump_json(exclude=exclude)


def test_chat_message_journal_bytes_are_unchanged():
    """Digests of existing chat turns (replay/conflict detection) do not move."""
    context = ClientEventV2(**BASE, type="chat.message", message="hi")
    assert journal(context) == (
        '{"conversation_id":"c-1","event_id":"e-1","type":"chat.message",'
        '"agent_ids":["enki"],"message":"hi"}'
    )
    llm = ClientEventV2(**BASE, type="chat.message", message="hi", response_mode="llm")
    assert journal(llm) == (
        '{"conversation_id":"c-1","event_id":"e-1","type":"chat.message",'
        '"response_mode":"llm","agent_ids":["enki"],"message":"hi"}'
    )
    assert set(context.model_dump()) == {
        "conversation_id",
        "event_id",
        "type",
        "response_mode",
        "agent_ids",
        "message",
    }


def test_activity_events_are_typed_and_digest_their_payload():
    start = ClientEventV2.model_validate({**BASE, "type": "activity.start", "activity": START})
    assert isinstance(start.activity, ActivityStartV2) and start.message is None
    close = ClientEventV2.model_validate({**BASE, "type": "activity.close", "activity": CLOSE})
    assert isinstance(close.activity, ActivityCloseV2)
    other = ClientEventV2.model_validate(
        {**BASE, "type": "activity.start", "activity": {**START, "references": {"draft": "d-2"}}}
    )
    digest = lambda e: hashlib.sha256(journal(e).encode()).hexdigest()
    assert len({digest(start), digest(close), digest(other)}) == 3
    assert "message" not in journal(start) and "activity" not in journal(context_event())


@pytest.mark.parametrize(
    "event",
    [
        {"type": "chat.message"},  # no message
        {"type": "chat.message", "message": "hi", "activity": START},
        {"type": "activity.start", "activity": CLOSE},
        {"type": "activity.close", "activity": START},
        {"type": "activity.start"},
        {"type": "activity.start", "activity": START, "message": "hi"},
        {"type": "activity.start", "activity": START, "response_mode": "llm"},
        {"type": "activity.start", "activity": START, "agent_ids": ["enki", "thoth"]},
        {"type": "activity.start", "activity": {**START, "key": "agent.creation_interview"}},
        {"type": "activity.start", "activity": {**START, "references": {"draft": "d", "x": "y"}}},
        {
            "type": "activity.start",
            "activity": {**START, "references": {"manuscript_session": "s"}},
        },
        {"type": "activity.start", "activity": {**START, "grants": ["*"]}},
        {"type": "activity.close", "activity": {**CLOSE, "reason": "completed"}},
        {"type": "activity.close", "activity": {**CLOSE, "reason": "switched"}},
        {"type": "activity.advance", "activity": START},
    ],
)
def test_malformed_or_unsupported_events_are_rejected(event):
    with pytest.raises(ValidationError):
        ClientEventV2.model_validate({**BASE, **event})


def context_event():
    return ClientEventV2(**BASE, type="chat.message", message="hi")


def view(**changes):
    return {"status": "active", "stage": "purpose", "answers": {}, **changes}


def test_prompt_is_the_current_stages_unanswered_fields():
    first = prompt(view(), AGENT_CREATION_INTERVIEW)
    assert first == {
        "stage": "purpose",
        "title": "Purpose",
        "guidance": AGENT_CREATION_INTERVIEW.stage("purpose").guidance,
        "fields": [{"key": "purpose", "title": "Purpose", "type": "text", "required": True}],
        "text": "Purpose: please provide Purpose.",
    }
    assert prompt(view(), AGENT_CREATION_INTERVIEW) == first
    answered = prompt(view(answers={"purpose": "Summarize contracts."}), AGENT_CREATION_INTERVIEW)
    assert answered["fields"] == [] and answered["text"] == "Purpose: ready to continue."
    assert prompt(view(status="completed"), AGENT_CREATION_INTERVIEW) is None
    for stage in AGENT_CREATION_INTERVIEW.stages:
        projected = prompt(view(stage=stage.key), AGENT_CREATION_INTERVIEW)
        assert [f["key"] for f in projected["fields"]] == stage.collects
        assert {f["key"] for f in projected["fields"] if f["required"]} == set(stage.required)
