"""MANUSCRIPT-0.1 definition contract and registry: strict validation, closed bounded
predicates, deterministic fingerprints, exact versions and immutable publication."""

import copy
from typing import Any

import pytest
from pydantic import ValidationError

from src.domains.agentverse.manuscripts.definitions import BUILTIN, register_builtin
from src.domains.agentverse.manuscripts.definitions.agent_creation_interview import (
    AGENT_CREATION_INTERVIEW,
)
from src.domains.agentverse.manuscripts.models import ManuscriptDefinition, evaluate
from src.domains.agentverse.manuscripts.registry import (
    PUBLISHED_FINGERPRINTS,
    ManuscriptConflict,
    ManuscriptRegistry,
    ManuscriptUnavailable,
)


def raw() -> dict[str, Any]:
    return copy.deepcopy(AGENT_CREATION_INTERVIEW.model_dump(mode="json"))


def build(data: dict[str, Any]) -> ManuscriptDefinition:
    return ManuscriptDefinition.model_validate(data)


def test_the_published_enki_manuscript_is_valid_and_pinned():
    definition = AGENT_CREATION_INTERVIEW
    assert definition.key() == "agent_creation_interview@1.0.0"
    assert PUBLISHED_FINGERPRINTS[definition.key()] == definition.fingerprint()
    assert definition.handoff.model_dump() == {
        "signal": "agent.draft.propose",
        "version": "1.0.0",
        "purpose": "Submit the completed creation as a draft proposal for human acceptance",
    }
    assert [stage.key for stage in definition.stages] == [
        "purpose",
        "identity",
        "composition",
        "capabilities",
        "configuration",
        "review",
    ]
    targets = {field.target for field in definition.fields if field.target}
    assert targets == {
        "/creation/name",
        "/creation/type",
        "/creation/role_name",
        "/creation/personality_key",
        "/creation/tools",
        "/creation/workflows",
        "/creation/rule_pack_ids",
        "/creation/prompt",
        "/creation/visibility",
    }


def test_fingerprints_are_deterministic_and_cover_guidance():
    first = AGENT_CREATION_INTERVIEW.fingerprint()
    assert build(raw()).fingerprint() == first
    changed = raw()
    changed["guidance"] += "\n"
    assert build(changed).fingerprint() != first
    retitled = raw()
    retitled["stages"][0]["title"] = "Why"
    assert build(retitled).fingerprint() != first


def test_registration_is_exact_idempotent_immutable_and_freezable():
    registry = ManuscriptRegistry("manuscripts-test")
    register_builtin(registry)
    register_builtin(registry)  # Reload-safe refresh.
    assert registry.registration_count == len(BUILTIN)
    fingerprint = AGENT_CREATION_INTERVIEW.fingerprint()
    assert registry.resolve("agent_creation_interview@1.0.0") is AGENT_CREATION_INTERVIEW
    assert registry.resolve("agent_creation_interview@1.0.0", fingerprint)
    for key, pin in (
        ("agent_creation_interview@1.0.0", "0" * 64),
        ("agent_creation_interview@1.0", None),
        ("agent_creation_interview@1.0.1", None),
        ("Agent_Creation_Interview@1.0.0", None),
        ("agent_creation_interview", None),
    ):
        with pytest.raises(ManuscriptUnavailable):
            registry.resolve(key, pin)
    changed = raw()
    changed["title"] = "Silently changed"
    with pytest.raises(ManuscriptConflict):  # Published pin.
        registry.register_definition(build(changed))
    unpinned = ManuscriptRegistry("manuscripts-unpinned", published={})
    unpinned.register_definition(AGENT_CREATION_INTERVIEW)
    with pytest.raises(ManuscriptConflict):  # Same version, different content.
        unpinned.register_definition(build(changed))
    newer = raw()
    newer["version"] = "1.1.0"
    unpinned.register_definition(build(newer))  # A new version is a new key.
    assert len(unpinned) == 2
    registry.freeze()
    with pytest.raises(RuntimeError):
        registry.register_definition(build(newer))
    outline = registry.outlines()[0]
    assert set(outline) == {"id", "version", "title", "fingerprint", "stages"}
    assert "guidance" not in str(outline)


def mutate(path: list[Any], value: Any) -> dict[str, Any]:
    data = raw()
    target = data
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    return data


@pytest.mark.parametrize(
    "data",
    [
        mutate(["stages", 5, "completion"], {"op": "python", "code": "import os"}),
        mutate(["stages", 5, "completion"], {"op": "equals", "field": "nope", "value": True}),
        mutate(
            ["stages", 5, "completion"], {"op": "equals", "field": "review_ready", "value": "yes"}
        ),
        mutate(
            ["stages", 5, "completion"], {"op": "equals", "field": "visibility", "value": "secret"}
        ),
        mutate(["stages", 5, "completion"], {"op": "catalog_contains", "field": "name"}),
        mutate(["stages", 5, "completion"], {"op": "in", "field": "tools", "values": ["x"]}),
        mutate(["stages", 5, "completion"], {"op": "expression", "value": "1+1"}),
        mutate(["stages", 0, "transitions"], [{"to": "nowhere", "when": {"op": "always"}}]),
        mutate(["stages", 5, "transitions"], [{"to": "purpose", "when": {"op": "always"}}]),
        mutate(["stages", 1, "collects"], ["name", "purpose"]),
        mutate(["stages", 1, "required"], ["prompt"]),
        mutate(["stages", 0, "transitions"], []),  # Later stages unreachable.
        # Optional-only stage without an explicit predicate would be skipped silently.
        mutate(["stages", 3, "required"], [])
        | {
            "stages": [
                *raw()["stages"][:3],
                {**raw()["stages"][3], "required": [], "completion": None},
                *raw()["stages"][4:],
            ]
        },
        mutate(["catalog_dependencies"], ["agent"]),
        mutate(["fields", 9, "options"], None),
        mutate(["fields", 0, "pattern"], "[unclosed"),
        mutate(["fields", 2, "catalog"], None),
        mutate(["prefill"], ["unknown"]),
        mutate(["initial_stage"], "missing"),
        {**raw(), "script": "fetch('https://example')"},
        {**raw(), "version": "1.0"},
        {**raw(), "id": "Agent Creation"},
    ],
)
def test_malformed_definitions_and_unsupported_operators_are_rejected(data):
    with pytest.raises(ValidationError):
        build(data)


def test_predicates_are_bounded_in_depth_and_size():
    deep: dict[str, Any] = {"op": "present", "field": "name"}
    for _ in range(9):
        deep = {"op": "not", "of": deep}
    with pytest.raises(ValidationError):
        build(mutate(["completion"], deep))
    wide = {"op": "all", "of": [{"op": "all", "of": [{"op": "always"}] * 16}] * 5}
    with pytest.raises(ValidationError):
        build(mutate(["completion"], wide))


def test_evaluation_is_pure_and_typed():
    completion = AGENT_CREATION_INTERVIEW.completion
    complete = {
        "name": "Summarizer",
        "agent_type": "chat",
        "role": "general",
        "soul": "empty_eva",
        "prompt": "Summarize contracts.",
        "visibility": "private",
        "review_ready": True,
    }
    assert evaluate(completion, complete)
    assert not evaluate(completion, {**complete, "review_ready": "true"})
    assert not evaluate(completion, {**complete, "role": ""})
    assert not evaluate(completion, {k: v for k, v in complete.items() if k != "soul"})


def test_guidance_markdown_is_inert_text():
    data = raw()
    data["guidance"] = "## purpose\n```python\nimport os; os.system('rm -rf /')\n```\n{{ exec }}"
    definition = build(data)
    assert definition.guidance.startswith("## purpose")
    # The machine definition (stages, predicates) is unchanged by any guidance text.
    assert definition.model_dump(exclude={"guidance"}) == AGENT_CREATION_INTERVIEW.model_dump(
        exclude={"guidance"}
    )
