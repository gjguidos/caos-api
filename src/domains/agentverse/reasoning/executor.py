"""In-process executor for registered reasoning tasks (REASONING-0.1A).

Not a capability endpoint: no CPBX signal, grant or route exists yet (REASONING-0.1B). The
executor holds only the task registry, the model policy and a ``ClientFactory`` accessor;
it has no exchange, authority, repository, Store, Genesis, Khepri, Manuscript or draft
handle, and it never passes tools to a model. Attribution and the deadline come from a
trusted ``ReasoningContext`` supplied by the calling boundary.

Order: bound the raw request -> strict request contract -> exact task -> strict task input
within the task's byte budget -> allow-listed, structured-output-capable model -> at most
``max_attempts`` attempts, each bounded by the task timeout *and* the caller's deadline ->
size bound -> strict local schema -> deterministic task verification. Cancellation is never
swallowed and no result is returned once the surrounding task is being cancelled. Prompts,
messages and model outputs are never logged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from src.base.infrastructure.ai.llm_usage import collect_llm_usage
from src.base.infrastructure.ai.structured_output import (
    StructuredOutputIncomplete,
    StructuredOutputRefused,
    StructuredOutputUnsupported,
)
from src.base.operations.v2.admission import canonical_json
from src.domains.agentverse.reasoning.models import (
    HARD_REQUEST_BYTES,
    FailureCode,
    ReasoningContext,
    ReasoningFailure,
    ReasoningIssue,
    ReasoningModelPolicy,
    ReasoningRequest,
    ReasoningResult,
    ReasoningTaskDefinition,
    ReasoningUsage,
)
from src.domains.agentverse.reasoning.registry import (
    ReasoningTaskRegistry,
    ReasoningTaskUnavailable,
)

logger = logging.getLogger(__name__)

# A new attempt needs at least this much of the caller's deadline left.
MIN_ATTEMPT_SECONDS = 1.0

INPUT_PREAMBLE = "Interpret the following JSON data according to your instructions."


class _AttemptFailed(Exception):
    def __init__(self, code: FailureCode, *, retry: bool) -> None:
        super().__init__(code)
        self.code: FailureCode = code
        self.retry = retry


class ReasoningExecutor:
    def __init__(
        self,
        *,
        registry: ReasoningTaskRegistry,
        client_factory: Callable[[], Any],
        model_policy: ReasoningModelPolicy,
        clock: Callable[[], float] = time.monotonic,
        min_attempt_seconds: float = MIN_ATTEMPT_SECONDS,
    ) -> None:
        self._registry = registry
        self._client_factory = client_factory
        self._policy = model_policy
        self._clock = clock
        self._min_attempt = min_attempt_seconds

    async def run(self, request: Any, *, context: ReasoningContext) -> ReasoningResult:
        try:
            raw = json.dumps(request, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            return self._failed(None, "reasoning.invalid_request")
        if len(raw.encode()) > HARD_REQUEST_BYTES:
            return self._failed(None, "reasoning.input_too_large")
        try:
            envelope = ReasoningRequest.model_validate_json(raw)
        except ValidationError:
            return self._failed(None, "reasoning.invalid_request")
        try:
            task = self._registry.resolve(envelope.task)
        except ReasoningTaskUnavailable:
            return self._failed(None, "reasoning.task_unavailable")
        task_input_json = canonical_json(envelope.input)
        if len(task_input_json.encode()) > task.budgets.max_input_bytes:
            return self._failed(task, "reasoning.input_too_large")
        try:
            task_input = task.input_model.model_validate_json(task_input_json)
        except ValidationError:
            return self._failed(task, "reasoning.invalid_request")

        model_key = self._policy.select(task.model_class, context.preferred_model, task=task.key())
        factory = self._client_factory()
        repo = factory.get_repo("llm", model_key) if factory is not None and model_key else None
        if repo is None or getattr(repo, "is_noop", False):
            return self._failed(task, "reasoning.model_unavailable")
        if getattr(repo, "supports_structured_output", False) is not True or not callable(
            getattr(repo, "get_structured_response_async", None)
        ):
            # Never fall back to free-text parsing.
            return self._failed(task, "reasoning.model_unsupported")

        usage_sink: list[dict[str, Any]] = []
        attempts = 0
        failure: FailureCode = "reasoning.deadline_exceeded"
        retryable = True
        message = [
            {
                "role": "user",
                "content": f"{INPUT_PREAMBLE}\n{canonical_json(task_input.model_dump(mode='json'))}",
            }
        ]
        while attempts < task.budgets.max_attempts:
            timeout = task.budgets.attempt_timeout_s
            if context.deadline is not None:
                remaining = context.deadline - self._clock()
                if remaining < self._min_attempt:
                    failure = "reasoning.deadline_exceeded" if attempts == 0 else failure
                    break
                timeout = min(timeout, remaining)
            attempts += 1
            try:
                output = await self._attempt(task, repo, message, timeout, usage_sink)
            except _AttemptFailed as exc:
                failure, retryable = exc.code, exc.retry
                if not exc.retry:
                    break
                continue
            except StructuredOutputRefused:
                return self._result(
                    task,
                    "empty",
                    None,
                    (ReasoningIssue(code="model.refused"),),
                    model_key,
                    usage_sink,
                    attempts,
                )
            self._check_cancel()
            verified = task.verify(task_input, output)
            self._log(task, context, verified.status, None, usage_sink, attempts)
            return self._result(
                task,
                verified.status,
                verified.output,
                verified.issues,
                model_key,
                usage_sink,
                attempts,
            )
        self._log(task, context, "failed", failure, usage_sink, attempts)
        return self._result(
            task,
            "failed",
            None,
            (),
            model_key,
            usage_sink,
            attempts,
            failure=ReasoningFailure(code=failure, retryable=retryable),
        )

    async def _attempt(
        self,
        task: ReasoningTaskDefinition,
        repo: Any,
        message: list[dict[str, Any]],
        timeout: float,
        usage_sink: list[dict[str, Any]],
    ) -> Any:
        try:
            with collect_llm_usage(usage_sink):
                async with asyncio.timeout(timeout):
                    text = await repo.get_structured_response_async(
                        message,
                        task.instructions,
                        schema_name=task.schema_name,
                        json_schema=task.output_json_schema,
                        max_output_tokens=task.budgets.max_output_tokens,
                    )
        except TimeoutError:
            self._check_cancel()
            raise _AttemptFailed("reasoning.timeout", retry=True) from None
        except StructuredOutputUnsupported:
            raise _AttemptFailed("reasoning.model_unsupported", retry=False) from None
        except StructuredOutputIncomplete:
            raise _AttemptFailed("reasoning.output_invalid", retry=True) from None
        except StructuredOutputRefused:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - provider errors must not expose bodies
            # 400/422: the provider rejected this request or schema; repeating cannot help.
            if getattr(exc, "status_code", None) in (400, 422):
                raise _AttemptFailed("reasoning.provider_rejected", retry=False) from None
            raise _AttemptFailed("reasoning.provider_failed", retry=True) from None
        self._check_cancel()
        if not isinstance(text, str) or len(text.encode()) > task.budgets.max_response_bytes:
            raise _AttemptFailed("reasoning.output_invalid", retry=True)
        try:
            return task.output_model.model_validate_json(text)
        except ValidationError:
            raise _AttemptFailed("reasoning.output_invalid", retry=True) from None

    @staticmethod
    def _check_cancel() -> None:
        current = asyncio.current_task()
        if current is not None and current.cancelling():
            raise asyncio.CancelledError

    @staticmethod
    def _usage(model_key: str | None, sink: list[dict[str, Any]], attempts: int) -> ReasoningUsage:
        tokens = [entry.get("tokens") or {} for entry in sink]
        last = sink[-1] if sink else {}
        return ReasoningUsage(
            model_key=model_key,
            provider=last.get("provider"),
            model=last.get("model"),
            prompt_tokens=sum(int(t.get("prompt_tokens", 0)) for t in tokens),
            completion_tokens=sum(int(t.get("completion_tokens", 0)) for t in tokens),
            total_tokens=sum(int(t.get("total_tokens", 0)) for t in tokens),
            latency_ms=sum(int(entry.get("latency_ms") or 0) for entry in sink),
            attempts=attempts,
        )

    def _result(
        self,
        task: ReasoningTaskDefinition,
        status: Any,
        output: dict[str, Any] | None,
        issues: tuple[ReasoningIssue, ...],
        model_key: str | None,
        sink: list[dict[str, Any]],
        attempts: int,
        *,
        failure: ReasoningFailure | None = None,
    ) -> ReasoningResult:
        return ReasoningResult(
            task=task.key(),
            task_fingerprint=task.fingerprint(),
            status=status,
            output=output,
            issues=issues,
            failure=failure,
            usage=self._usage(model_key, sink, attempts),
        )

    @staticmethod
    def _failed(task: ReasoningTaskDefinition | None, code: FailureCode) -> ReasoningResult:
        return ReasoningResult(
            task=task.key() if task else None,
            task_fingerprint=task.fingerprint() if task else None,
            status="failed",
            failure=ReasoningFailure(code=code, retryable=False),
        )

    def _log(
        self,
        task: ReasoningTaskDefinition,
        context: ReasoningContext,
        status: str,
        failure: str | None,
        sink: list[dict[str, Any]],
        attempts: int,
    ) -> None:
        # Identifiers, codes and counts only: never prompts, messages or outputs.
        usage = self._usage(None, sink, attempts)
        logger.info(
            "reasoning task=%s status=%s failure=%s attempts=%d tokens=%d correlation=%s",
            task.key(),
            status,
            failure,
            attempts,
            usage.total_tokens,
            context.correlation_id,
        )
