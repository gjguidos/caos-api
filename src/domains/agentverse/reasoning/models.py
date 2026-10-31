"""REASONING-0.1A contracts: registered tasks, requests, results and model policy.

Reasoning is an interpretation resource. A caller names one registered task and supplies
its typed input; it cannot supply instructions, a model, tools, identity or authority.
Results are advisory and untrusted: schema conformance makes them well-formed, never true.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints

from src.base.operations.v2.admission import sha256_hex

REQUEST_CONTRACT = "caos.reasoning.request"
RESULT_CONTRACT = "caos.reasoning.result"
TASK_CONTRACT = "caos.reasoning.task"
# Absolute ceilings; every task declares lower budgets.
HARD_REQUEST_BYTES = 32 * 1024
HARD_RESPONSE_BYTES = 64 * 1024
HARD_OUTPUT_TOKENS = 4096
HARD_ATTEMPTS = 3
HARD_ATTEMPT_TIMEOUT_S = 60.0

TASK_KEY_PATTERN = (
    r"^[a-z][a-z0-9_]{0,31}(\.[a-z][a-z0-9_]{0,31}){0,3}"
    r"@(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"
)
TaskKey = Annotated[str, StringConstraints(pattern=TASK_KEY_PATTERN, max_length=100)]
RequestKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{16,128}$")]

FailureCode = Literal[
    "reasoning.invalid_request",
    "reasoning.input_too_large",
    "reasoning.task_unavailable",
    "reasoning.model_unavailable",
    "reasoning.model_unsupported",
    "reasoning.deadline_exceeded",
    "reasoning.timeout",
    "reasoning.provider_failed",
    # The provider rejected the request itself (e.g. an unsupported JSON Schema): not retried.
    "reasoning.provider_rejected",
    "reasoning.output_invalid",
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


# --- Task definitions ---------------------------------------------------------------------


@dataclass(frozen=True)
class ReasoningBudgets:
    max_input_bytes: int
    max_output_tokens: int
    max_response_bytes: int
    attempt_timeout_s: float
    max_attempts: int

    def __post_init__(self) -> None:
        if not 1 <= self.max_input_bytes <= HARD_REQUEST_BYTES:
            raise ValueError("Task input budget exceeds the hard request cap")
        if not 1 <= self.max_output_tokens <= HARD_OUTPUT_TOKENS:
            raise ValueError("Invalid output-token budget")
        if not 1 <= self.max_response_bytes <= HARD_RESPONSE_BYTES:
            raise ValueError("Invalid response-size budget")
        if not 0 < self.attempt_timeout_s <= HARD_ATTEMPT_TIMEOUT_S:
            raise ValueError("Invalid attempt timeout")
        if not 1 <= self.max_attempts <= HARD_ATTEMPTS:
            raise ValueError("Invalid attempt budget")


@dataclass(frozen=True)
class Verified:
    """A task verifier's deterministic verdict over one parsed model output."""

    status: Literal["interpreted", "unresolved", "empty"]
    output: dict[str, Any]
    issues: tuple[ReasoningIssue, ...] = ()


Verifier = Callable[[Any, Any], Verified]


@dataclass(frozen=True)
class ReasoningTaskDefinition:
    """A server-owned, versioned reasoning task (``caos.reasoning.task`` v1).

    ``instructions`` and ``output_json_schema`` are fixed in source and fingerprinted;
    ``verifier_version`` must change whenever the verifier's semantics change. The
    fingerprint deliberately excludes library-generated schemas so dependency upgrades
    cannot silently change a published task.
    """

    id: str
    version: str
    title: str
    instructions: str
    input_model: type[BaseModel]
    input_contract: str
    output_model: type[BaseModel]
    output_contract: str
    output_json_schema: dict[str, Any]
    schema_name: str
    model_class: str
    budgets: ReasoningBudgets
    verify: Verifier
    verifier_version: str
    description: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)

    def key(self) -> str:
        return f"{self.id}@{self.version}"

    def fingerprint(self) -> str:
        return sha256_hex(
            {
                "contract": TASK_CONTRACT,
                "schema_version": 1,
                "id": self.id,
                "version": self.version,
                "title": self.title,
                "instructions": self.instructions,
                "input_contract": self.input_contract,
                "output_contract": self.output_contract,
                "output_json_schema": self.output_json_schema,
                "schema_name": self.schema_name,
                "model_class": self.model_class,
                "budgets": {
                    "max_input_bytes": self.budgets.max_input_bytes,
                    "max_output_tokens": self.budgets.max_output_tokens,
                    "max_response_bytes": self.budgets.max_response_bytes,
                    "attempt_timeout_s": self.budgets.attempt_timeout_s,
                    "max_attempts": self.budgets.max_attempts,
                },
                "verifier_version": self.verifier_version,
            }
        )


# --- Model policy ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelClassPolicy:
    """Reviewed allow-list of ``ClientFactory`` llm keys for one task model class."""

    allowed: tuple[str, ...]
    default: str

    def __post_init__(self) -> None:
        if not self.allowed or len(set(self.allowed)) != len(self.allowed):
            raise ValueError("A model class needs a non-empty, unique allow-list")
        if self.default not in self.allowed:
            raise ValueError("The default model must be allow-listed")


@dataclass(frozen=True)
class ReasoningModelPolicy:
    """Trusted deployment configuration; never derived from callers or task inputs.

    ``tasks`` (exact ``task_id@version`` keys, REASONING-0.1B reviewed configuration) take
    precedence over ``classes``; a task with neither has no model and cannot run.
    """

    classes: Mapping[str, ModelClassPolicy] = field(default_factory=dict)
    tasks: Mapping[str, ModelClassPolicy] = field(default_factory=dict)

    def select(
        self, model_class: str, preferred: str | None, *, task: str | None = None
    ) -> str | None:
        """The preferred key only if allow-listed, else the configured default."""
        policy = self.tasks.get(task) if task is not None else None
        if policy is None:
            policy = self.classes.get(model_class)
        if policy is None:
            return None
        return preferred if preferred in policy.allowed else policy.default


# --- Requests and context -------------------------------------------------------------------


class ReasoningRequest(_Strict):
    """Identity-free: no instructions, model, provider, tools, tenant, user or grants."""

    contract: Literal["caos.reasoning.request"]
    schema_version: StrictInt = Field(ge=1, le=1)
    task: TaskKey
    request_key: RequestKey
    input: dict[str, Any]


@dataclass(frozen=True)
class ReasoningContext:
    """Trusted, in-process attribution and limits supplied by the calling boundary.

    In REASONING-0.1B these come from a verified, session-origin UTU permit. ``deadline``
    is an absolute ``time.monotonic()`` value; ``preferred_model`` is the source Agent's
    configured llm key and is honoured only inside the task's allow-list.
    """

    tenant_id: str
    requester_id: str
    correlation_id: str
    source_agent_id: str | None = None
    deadline: float | None = None
    preferred_model: str | None = None


# --- Results --------------------------------------------------------------------------------


class ReasoningIssue(_Strict):
    code: Annotated[str, StringConstraints(pattern=r"^[a-z_]+\.[a-z_]+$")]
    field: Annotated[str, StringConstraints(max_length=64)] | None = None


class ReasoningFailure(_Strict):
    code: FailureCode
    retryable: bool = False


class ReasoningUsage(_Strict):
    model_key: str | None = None
    provider: str | None = None
    model: str | None = None
    prompt_tokens: Annotated[int, Field(ge=0)] = 0
    completion_tokens: Annotated[int, Field(ge=0)] = 0
    total_tokens: Annotated[int, Field(ge=0)] = 0
    latency_ms: Annotated[int, Field(ge=0)] = 0
    attempts: Annotated[int, Field(ge=0, le=HARD_ATTEMPTS)] = 0


class ReasoningAuthorizes(_Strict):
    """Nothing a reasoning result says can authorize anything."""

    draft_write: Literal[False] = False
    manuscript_answer: Literal[False] = False
    proposal: Literal[False] = False
    approval: Literal[False] = False
    creation: Literal[False] = False
    activation: Literal[False] = False
    execution: Literal[False] = False


class ReasoningResult(_Strict):
    contract: Literal["caos.reasoning.result"] = "caos.reasoning.result"
    schema_version: Literal[1] = 1
    task: str | None = None
    task_fingerprint: str | None = None
    status: Literal["interpreted", "unresolved", "empty", "failed"]
    output: dict[str, Any] | None = None
    issues: tuple[ReasoningIssue, ...] = ()
    failure: ReasoningFailure | None = None
    usage: ReasoningUsage = Field(default_factory=ReasoningUsage)
    authoritative: Literal[False] = False
    authorizes: ReasoningAuthorizes = Field(default_factory=ReasoningAuthorizes)
