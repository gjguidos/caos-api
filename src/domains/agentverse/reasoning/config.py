"""Reviewed Reasoning model policy (``config/reasoning.local.json``, REASONING-0.1B).

Per published task: its pinned fingerprint, model class, an explicit allow-list of
``ClientFactory`` llm keys and a default inside it. Validated at trusted startup and fail
closed: the registry must be frozen, every configured task must be the published content,
every registered task must be configured (no unspecified default), and every allow-listed
key must name an existing, real repository that declares structured-output support. No
fuzzy key resolution and no fallback model. Callers never choose instructions, provider
configuration or models.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints, model_validator

from src.domains.agentverse.reasoning.models import (
    TASK_KEY_PATTERN,
    ModelClassPolicy,
    ReasoningModelPolicy,
)
from src.domains.agentverse.reasoning.registry import (
    ReasoningTaskRegistry,
    ReasoningTaskUnavailable,
)

DEFAULT_PATH = "config/reasoning.local.json"
MAX_CONFIG_BYTES = 64 * 1024
ModelKey = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class TaskModelConfig(_Strict):
    fingerprint: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    model_class: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    allowed_models: Annotated[tuple[ModelKey, ...], Field(min_length=1, max_length=8)]
    default_model: ModelKey

    @model_validator(mode="after")
    def _closed(self) -> Self:
        if len(set(self.allowed_models)) != len(self.allowed_models):
            raise ValueError("Duplicate allowed model")
        if self.default_model not in self.allowed_models:
            raise ValueError("The default model must be allow-listed")
        return self


class ReasoningConfig(_Strict):
    contract: Literal["caos.reasoning.config"]
    schema_version: StrictInt = Field(ge=1, le=1)
    revision: StrictInt = Field(ge=1)
    tasks: Annotated[
        dict[Annotated[str, StringConstraints(pattern=TASK_KEY_PATTERN)], TaskModelConfig],
        Field(min_length=1, max_length=32),
    ]

    def model_policy(self) -> ReasoningModelPolicy:
        return ReasoningModelPolicy(
            tasks={
                key: ModelClassPolicy(allowed=entry.allowed_models, default=entry.default_model)
                for key, entry in self.tasks.items()
            }
        )


def config_path() -> Path:
    return Path(os.environ.get("REASONING_POLICY_FILE") or DEFAULT_PATH)


def load_reasoning_config(path: Path | None = None) -> ReasoningConfig:
    path = path or config_path()
    raw = path.read_bytes()
    if len(raw) > MAX_CONFIG_BYTES:
        raise ValueError("Reasoning configuration too large")
    return ReasoningConfig.model_validate_json(raw)


def validate_registry(config: ReasoningConfig, registry: ReasoningTaskRegistry) -> None:
    """Fail closed unless configuration and the frozen registry agree exactly."""
    from src.domains.agentverse.reasoning.tasks import PUBLISHED_FINGERPRINTS

    if not registry.frozen:
        raise ValueError("Reasoning task registry must be frozen")
    registered = set(registry.keys())
    if registered != set(config.tasks):
        raise ValueError("Every registered reasoning task needs exactly one reviewed model policy")
    for key, entry in config.tasks.items():
        if PUBLISHED_FINGERPRINTS.get(key) != entry.fingerprint:
            raise ValueError(f"Reasoning task {key} is not the published content")
        try:
            task = registry.resolve(key, entry.fingerprint)
        except ReasoningTaskUnavailable as exc:
            raise ValueError(f"Reasoning task {key} fingerprint mismatch") from exc
        if task.model_class != entry.model_class:
            raise ValueError(f"Reasoning task {key} model class mismatch")


def validate_models(config: ReasoningConfig, factory: Any) -> None:
    """Every allow-listed key is an exact, real, structured-output-capable repository."""
    if factory is None:
        raise ValueError("Model catalog unavailable")
    for entry in config.tasks.values():
        for key in entry.allowed_models:
            repo = factory.get_repo("llm", key)  # Exact key; no fuzzy resolution.
            if repo is None or getattr(repo, "is_noop", False):
                raise ValueError(f"Reasoning model {key} is not configured")
            if getattr(repo, "supports_structured_output", False) is not True or not callable(
                getattr(repo, "get_structured_response_async", None)
            ):
                raise ValueError(f"Reasoning model {key} lacks structured output")
