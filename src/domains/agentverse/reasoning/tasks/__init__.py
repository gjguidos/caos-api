"""Published reasoning tasks and their pinned fingerprints."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.domains.agentverse.reasoning.models import ReasoningTaskDefinition
    from src.domains.agentverse.reasoning.registry import ReasoningTaskRegistry

# Published versions are immutable: their fingerprint is recorded here when published and
# checked on every registration, so instructions or schemas can never change silently.
PUBLISHED_FINGERPRINTS: dict[str, str] = {
    "manuscript.answer_extraction@1.0.0": (
        "06354ea745eece00466bef7f5a86841765a00fe24d50604727fa2785cd049b9f"
    ),
}


def builtin_tasks() -> tuple[ReasoningTaskDefinition, ...]:
    """The published task definitions, in publication order."""
    from src.domains.agentverse.reasoning.tasks.manuscript_answer_extraction import (
        MANUSCRIPT_ANSWER_EXTRACTION,
    )

    return (MANUSCRIPT_ANSWER_EXTRACTION,)


def register_builtin(registry: ReasoningTaskRegistry) -> None:
    for task in builtin_tasks():
        registry.register_task(task)
