"""Opt-in, bounded real-model smoke test for the reviewed Reasoning model (REASONING-0.1B).

Skipped unless ``REASONING_REAL_MODEL_SMOKE=1`` and provider credentials are configured.
Uses only synthetic data, the production repository builder and the reviewed
``config/reasoning.local.json`` default model. At most three short provider calls. It
checks provider acceptance of the strict JSON Schema, output-token control and parsing;
the authoritative local schema is never relaxed to fit a provider.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from src.base.config.ai_models_config import AiModelsConfig
from src.base.infrastructure.ai.client_factory import ClientFactory
from src.base.infrastructure.ai.structured_output import StructuredOutputIncomplete
from src.base.infrastructure.ai.utils.build_openai_llm_repositories import (
    build_openai_repositories,
)
from src.domains.agentverse.reasoning.config import load_reasoning_config
from src.domains.agentverse.reasoning.executor import ReasoningExecutor
from src.domains.agentverse.reasoning.models import ReasoningContext
from src.domains.agentverse.reasoning.registry import ReasoningTaskRegistry
from src.domains.agentverse.reasoning.tasks import register_builtin
from src.domains.agentverse.reasoning.tasks.manuscript_answer_extraction import (
    MANUSCRIPT_ANSWER_EXTRACTION as TASK,
)
from src.domains.agentverse.reasoning.tests.test_reasoning import KEY, request

pytestmark = pytest.mark.skipif(
    os.environ.get("REASONING_REAL_MODEL_SMOKE") != "1",
    reason="Opt-in real-model smoke test (REASONING_REAL_MODEL_SMOKE=1)",
)


def _repo():
    config = load_reasoning_config(Path("config/reasoning.local.json"))
    key = config.tasks[KEY].default_model
    repos = build_openai_repositories(AiModelsConfig()).get("llm", {})
    if key not in repos:
        pytest.skip(f"Configured model {key} is not available in this environment")
    return config, key, repos[key]


@pytest.mark.asyncio
async def test_configured_model_accepts_the_strict_schema_and_parses() -> None:
    config, key, repo = _repo()
    registry = ReasoningTaskRegistry(f"reasoning-smoke-{time.monotonic_ns()}")
    register_builtin(registry)
    registry.freeze()
    factory = ClientFactory({"llm": {key: repo}}, {"llm": key})
    executor = ReasoningExecutor(
        registry=registry, client_factory=lambda: factory, model_policy=config.model_policy()
    )
    result = await executor.run(
        request(),
        context=ReasoningContext(
            tenant_id="smoke", requester_id="smoke", correlation_id="reasoning-smoke",
            deadline=time.monotonic() + 45,
        ),
    )  # fmt: skip
    # Any non-failed status proves schema acceptance, parsing and local verification.
    assert result.status != "failed", result.failure
    assert result.usage.model_key == key and result.usage.attempts >= 1
    assert (
        0 < result.usage.completion_tokens <= TASK.budgets.max_output_tokens * result.usage.attempts
    )
    # Smoke evidence: identifiers and counts only.
    print(
        "SMOKE", key, result.status, result.usage.model_dump(),
        [a["field"] for a in (result.output or {}).get("accepted", [])],
    )  # fmt: skip


@pytest.mark.asyncio
async def test_output_token_control_is_enforced_by_the_provider() -> None:
    _, _, repo = _repo()
    with pytest.raises(StructuredOutputIncomplete):
        await repo.get_structured_response_async(
            [{"role": "user", "content": "List twenty distinct colors with one-line notes."}],
            "Return the colors as items.",
            schema_name="smoke_colors",
            json_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["items"],
                "properties": {"items": {"type": "array", "items": {"type": "string"}}},
            },
            max_output_tokens=16,
        )
