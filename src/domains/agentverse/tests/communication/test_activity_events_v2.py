"""COORD-0.1/0.2 client event contract, deterministic prompt and answer filtering (no
infrastructure)."""

import hashlib

import pytest
from pydantic import ValidationError

from src.base.operations.v2.admission import canonical_json
from src.domains.agentverse.entities.conversation_v2 import (
    ActivityAnswerV2,
    ActivityCloseV2,
    ActivityStartV2,
    ClientEventV2,
)
from src.domains.agentverse.manuscripts.definitions import AGENT_CREATION_INTERVIEW
from src.domains.agentverse.reasoning.tasks.manuscript_answer_extraction import (
    MANUSCRIPT_ANSWER_EXTRACTION,
    AnswerExtractionInput,
)
from src.domains.agentverse.services.creation_interview_coordinator_v2 import (
    _changes,
    _filter,
    _guidance,
    _summary,
    _task_input,
    _within,
    prompt,
)

BASE = {"conversation_id": "c-1", "event_id": "e-1", "agent_ids": ["enki"]}
START = {"key": "agent.creation_interview@1.0.0", "references": {"draft": "d-1"}}
CLOSE = {"context_id": "ctx-1", "reason": "abandoned"}
ANSWER = {"context_id": "ctx-1", "expected_revision": 3}
CONFIRM = {"context_id": "ctx-1", "expected_revision": 4, "field": "review_ready", "value": True}
COMPLETE = {"context_id": "ctx-1", "expected_revision": 7}
M = AGENT_CREATION_INTERVIEW


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
        # activity.answer: exactly a message, a context and the answered revision.
        {"type": "activity.answer", "activity": ANSWER},
        {"type": "activity.answer", "message": "hi", "activity": CLOSE},
        {"type": "activity.answer", "message": "hi", "activity": {"context_id": "ctx-1"}},
        {
            "type": "activity.answer",
            "message": "hi",
            "activity": {**ANSWER, "expected_revision": 0},
        },
        {
            "type": "activity.answer",
            "message": "hi",
            "activity": {**ANSWER, "expected_revision": "3"},
        },
        {"type": "activity.answer", "message": "hi", "activity": {**ANSWER, "stage": "review"}},
        {"type": "activity.answer", "message": "hi", "activity": {**ANSWER, "answers": {}}},
        {"type": "activity.answer", "message": "hi", "activity": ANSWER, "response_mode": "llm"},
        {"type": "activity.answer", "message": "hi", "activity": ANSWER, "agent_ids": ["a", "b"]},
        {"type": "activity.start", "message": "hi", "activity": ANSWER},
        # activity.confirm: exactly one explicit ``true``; never a message.
        {"type": "activity.confirm", "activity": {**CONFIRM, "value": False}},
        {"type": "activity.confirm", "activity": {**CONFIRM, "value": "true"}},
        {
            "type": "activity.confirm",
            "activity": {k: v for k, v in CONFIRM.items() if k != "value"},
        },
        {"type": "activity.confirm", "activity": {**CONFIRM, "field": "Review Ready"}},
        {"type": "activity.confirm", "activity": {**CONFIRM, "presentation_digest": "abc"}},
        {"type": "activity.confirm", "activity": {**CONFIRM, "answers": {"x": True}}},
        {"type": "activity.confirm", "message": "yes", "activity": CONFIRM},
        # activity.complete: a context and the revision only; never shaped as an answer.
        {"type": "activity.complete", "message": "done", "activity": COMPLETE},
        {"type": "activity.complete", "activity": {**COMPLETE, "proposal": {"draft_id": "d"}}},
        {"type": "activity.complete", "activity": CONFIRM},
        {"type": "activity.answer", "activity": COMPLETE},
        {"type": "activity.close", "activity": {"context_id": "ctx-1", "reason": "completed"}},
    ],
)
def test_malformed_or_unsupported_events_are_rejected(event):
    with pytest.raises(ValidationError):
        ClientEventV2.model_validate({**BASE, **event})


def test_answer_event_binds_a_context_and_revision():
    event = ClientEventV2.model_validate(
        {
            **BASE,
            "type": "activity.answer",
            "message": "It summarizes contracts.",
            "activity": ANSWER,
        }
    )
    assert isinstance(event.activity, ActivityAnswerV2)
    assert (event.activity.context_id, event.activity.expected_revision) == ("ctx-1", 3)
    assert journal(event) == (
        '{"conversation_id":"c-1","event_id":"e-1","type":"activity.answer",'
        '"agent_ids":["enki"],"message":"It summarizes contracts.",'
        '"activity":{"context_id":"ctx-1","expected_revision":3}}'
    )


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


def test_prompt_marks_boolean_confirmations():
    fields = prompt(view(stage="capabilities"), M)["fields"]
    assert [f["key"] for f in fields if f.get("confirmation")] == ["capabilities_reviewed"]
    assert prompt(view(stage="review"), M)["fields"][0]["confirmation"] is True


def test_summary_reports_pending_confirmations_only_when_nothing_else_is_missing():
    base = {
        "session_id": "s",
        "revision": 4,
        "status": "active",
        "completed_stages": [],
        "ready_to_complete": False,
    }
    pending = _summary({**base, "stage": "capabilities", "missing": ["capabilities_reviewed"]}, M)
    assert pending["confirmations_pending"] is True
    other = _summary({**base, "stage": "purpose", "missing": ["purpose"]}, M)
    assert other["confirmations_pending"] is False


def test_guidance_excerpt_is_the_stage_section_only():
    excerpt = _guidance(M, M.stage("identity"))
    assert excerpt.startswith("Propose a short") and "##" not in excerpt
    assert "Agent Types" not in excerpt


def fields(stage):
    return [M.field(k) for k in M.stage(stage).collects if M.field(k).type != "boolean"]


def task_view(stage, answers=None):
    return {"stage": stage, "answers": answers or {}}


def test_task_input_offers_only_current_stage_fields_within_limits():
    stage = M.stage("configuration")
    task_input, trimmed = _task_input(
        "Be concise.",
        M,
        stage,
        fields("configuration"),
        task_view("configuration", {"purpose": "x" * 20}),
        {},
    )
    AnswerExtractionInput.model_validate_json(canonical_json(task_input))  # As the executor.
    assert [f["key"] for f in task_input["fields"]] == ["prompt", "visibility"]
    # The 20 000-character Instructions field is offered within the task's 2 000 ceiling.
    assert task_input["fields"][0]["max_length"] == 2000
    assert task_input["current_answers"] == {} and trimmed == set()


def test_large_catalogs_are_trimmed_deterministically_and_reported_incomplete():
    stage = M.stage("capabilities")
    options = {
        key: [{"key": f"{key}-{i:03}", "title": "T" * 100} for i in range(50)]
        for key in ("tools", "workflows", "rule_packs")
    }
    first = _task_input(
        "Use the RAG tool.", M, stage, fields("capabilities"), task_view("capabilities"), options
    )
    again = _task_input(
        "Use the RAG tool.", M, stage, fields("capabilities"), task_view("capabilities"), options
    )
    assert first == again and first is not None
    task_input, trimmed = first
    assert trimmed and trimmed <= {"tools", "workflows", "rule_packs"}
    budget = MANUSCRIPT_ANSWER_EXTRACTION.budgets.max_input_bytes
    assert len(canonical_json(task_input).encode()) <= budget
    AnswerExtractionInput.model_validate_json(canonical_json(task_input))
    # A message that alone exceeds the byte budget is never truncated: no input.
    assert (
        _task_input(
            "é" * 4000 + "x" * 500,
            M,
            M.stage("purpose"),
            fields("purpose"),
            task_view("purpose"),
            {},
        )
        is None
    )


def result(*accepted, unresolved=(), clarify=(), status="interpreted", issues=()):
    return {
        "status": status,
        "output": {
            "accepted": list(accepted),
            "unresolved": list(unresolved),
            "clarification_fields": list(clarify),
        },
        "issues": [{"code": c} for c in issues],
    }


def acc(field, value, confidence="high"):
    return {"field": field, "value": value, "confidence": confidence, "evidence": "e"}


def test_filter_accepts_only_bounded_current_stage_candidates():
    offered = fields("composition")
    options = {"agent_type": [{"key": "chat", "title": "Chat"}], "role": [], "soul": []}
    accepted, summary = _filter(
        result(
            acc("agent_type", "chat"),
            acc("name", "Leaked"),  # Another stage: never forwarded or echoed.
            acc("role", "admin"),  # Not an offered catalog key.
            acc("soul", None),
        ),
        offered,
        options,
        set(),
    )
    assert accepted == {"agent_type": "chat"}
    assert summary["rejected"] == {
        "role": "candidate.out_of_bounds",
        "soul": "candidate.value_missing",
    }
    assert summary["issues"] == ["candidate.field_out_of_stage"]
    assert "Leaked" not in str(summary) and "name" not in str(summary["rejected"])


def test_filter_routes_low_confidence_ambiguity_and_conflicts_to_clarification():
    offered = fields("configuration")
    accepted, summary = _filter(
        result(
            acc("prompt", "Summarize contracts briefly.", confidence="low"),
            acc("visibility", "private"),
            acc("visibility", "public"),
            unresolved=[{"field": "visibility", "reason": "ambiguous", "alternatives": ["x"]}],
        ),
        offered,
        {},
        set(),
    )
    assert accepted == {}
    assert summary["unresolved"] == [
        {"field": "prompt", "reason": "low_confidence"},
        {"field": "visibility", "reason": "conflicting"},
    ]
    _, ambiguous = _filter(
        result(
            unresolved=[
                {
                    "field": "visibility",
                    "reason": "ambiguous",
                    "alternatives": ["private", "the model's own text"],
                }
            ],
            status="unresolved",
        ),
        offered,
        {},
        set(),
    )
    # Only offered option values are reported back, never free model text.
    assert ambiguous["unresolved"] == [
        {"field": "visibility", "reason": "ambiguous", "alternatives": ["private"]}
    ]


def test_filter_reports_failures_and_incomplete_catalogs_without_values():
    failed_accepted, failed = _filter(
        {"status": "failed", "failure": {"code": "reasoning.timeout", "retryable": True}},
        fields("purpose"),
        {},
        set(),
    )
    assert failed_accepted == {} and failed["status"] == "failed"
    assert failed["failure"] == {"code": "reasoning.timeout", "retryable": True}
    _, incomplete = _filter(
        result(status="empty"), fields("capabilities"), {"tools": []}, {"tools"}
    )
    assert incomplete["catalog_incomplete"] == ["tools"]
    assert incomplete["unresolved"] == [{"field": "tools", "reason": "catalog_incomplete"}]


def test_value_bounds_never_truncate_long_instructions():
    spec = M.field("prompt")
    assert _within(spec, "x" * 2000, {}) and not _within(spec, "x" * 2001, {})
    assert not _within(M.field("tools"), ["a", "a"], {"tools": [{"key": "a", "title": "A"}]})
    assert _within(M.field("visibility"), "private", {})
    assert not _within(M.field("visibility"), "secret", {})


def test_confirm_and_complete_events_are_typed_by_their_event_type():
    confirm = ClientEventV2.model_validate(
        {**BASE, "type": "activity.confirm", "activity": CONFIRM}
    )
    assert journal(confirm) == (
        '{"conversation_id":"c-1","event_id":"e-1","type":"activity.confirm",'
        '"agent_ids":["enki"],"activity":{"context_id":"ctx-1","expected_revision":4,'
        '"field":"review_ready","value":true}}'
    )
    digest = "a" * 64
    bound = ClientEventV2.model_validate_json(
        canonical_json(
            {
                **BASE,
                "type": "activity.confirm",
                "activity": {**CONFIRM, "presentation_digest": digest},
            }
        )
    )
    assert bound.activity.presentation_digest == digest
    complete = ClientEventV2.model_validate(
        {**BASE, "type": "activity.complete", "activity": COMPLETE}
    )
    # Same shape as an answer's payload: the event type alone selects the model.
    assert type(complete.activity).__name__ == "ActivityCompleteV2"
    assert "message" not in journal(complete)


def creation(**fields):
    return {
        "contract": "caos.agent_creation_draft",
        "schema_version": 1,
        "resource_type": "agent",
        "creation": fields,
    }


def test_proposal_changes_are_exactly_the_differing_interview_fields():
    artifact = creation(
        name="Contract Summarizer",
        type="chat",
        tools=[{"key": "rag", "config": {}}, {"key": "date_tool", "config": {}}],
        prompt="Summarize each contract.",
        visibility="private",
    )
    draft = {
        "name": "Contract Summarizer",
        "whitelisted_groups": ["legal-team"],  # Outside the interview: never touched.
        "tools": [{"key": "rag", "config": {"top_k": 3}}],
        "gui_components": [],
    }
    assert _changes(artifact, draft) == {
        "type": "chat",
        # The existing Tool keeps its configuration; the new one keeps the artifact's.
        "tools": [{"key": "rag", "config": {"top_k": 3}}, {"key": "date_tool", "config": {}}],
        "prompt": "Summarize each contract.",
        "visibility": "private",
    }


def test_an_empty_manuscript_configuration_never_erases_an_existing_one():
    artifact = creation(name="A", tools=[{"key": "rag", "config": {}}])
    draft = {"name": "A", "tools": [{"key": "rag", "config": {"top_k": 3}}]}
    assert _changes(artifact, draft) == {}  # Already current after preservation.
    # Selection order is the artifact's; unselected Tools are not part of the selection.
    ordered = creation(tools=[{"key": "b", "config": {}}, {"key": "a", "config": {}}])
    existing = {"tools": [{"key": "a", "config": {"x": 1}}, {"key": "c", "config": {"y": 2}}]}
    assert _changes(ordered, existing)["tools"] == [
        {"key": "b", "config": {}},
        {"key": "a", "config": {"x": 1}},
    ]


@pytest.mark.parametrize(
    "existing",
    [
        # Duplicated identity: which configuration to keep is undecidable.
        [{"key": "rag", "config": {"top_k": 1}}, {"key": "rag", "config": {"top_k": 2}}],
        # Equal only after canonicalization: not the same reference.
        [{"key": "RAG", "config": {"top_k": 1}}],
    ],
)
def test_ambiguous_tool_identities_are_refused(existing):
    from src.domains.agentverse.services.creation_interview_coordinator_v2 import (
        ToolIdentityAmbiguous,
    )

    with pytest.raises(ToolIdentityAmbiguous):
        _changes(creation(tools=[{"key": "rag", "config": {}}]), {"tools": existing})
