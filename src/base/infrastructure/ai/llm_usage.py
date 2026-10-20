"""Side-channel for per-call LLM usage.

``get_response_async`` returns a bare string and a long chain of callers depends
on that contract, so token counts and the model that actually ran cannot be
returned. Instead the client appends them to a sink held in a context variable:
whoever opens a collection scope gets every call made underneath it, in call
order, without a single intermediate signature changing.

Correlation is by call order, not by return value. That is the fragile part of
the design and the reason the sink records the model per entry.

The sink is read once, when the turn ends. ``asyncio.create_task`` copies the
context, so a task spawned inside the scope keeps appending after the report
was built - those entries are simply not in it. Anything whose usage must be
counted has to be awaited inside the scope.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_llm_usage_sink: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "llm_usage_sink", default=None
)


@contextmanager
def collect_llm_usage(sink: list[dict[str, Any]]) -> Iterator[list[dict[str, Any]]]:
    """Collect every LLM call made inside this scope into ``sink``."""
    token = _llm_usage_sink.set(sink)
    try:
        yield sink
    finally:
        _llm_usage_sink.reset(token)


def normalize_usage(usage: Any) -> dict[str, int] | None:
    """Flatten a provider usage object to prompt/completion/total token counts."""
    if usage is None:
        return None

    def _get(*names: str) -> Any:
        for name in names:
            value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
            if value is not None:
                return value
        return None

    try:
        prompt = int(_get("input_tokens", "prompt_tokens") or 0)
        completion = int(_get("output_tokens", "completion_tokens") or 0)
        total = int(_get("total_tokens") or (prompt + completion))
    except (TypeError, ValueError):
        return None

    if not (prompt or completion or total):
        return None
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}


def record_llm_call(
    *,
    model: str | None,
    usage: Any = None,
    latency_ms: int | None = None,
    provider: str | None = None,
) -> None:
    """Append one LLM call to the active sink, if any. Never raises."""
    sink = _llm_usage_sink.get()
    if sink is None:
        return
    try:
        sink.append(
            {
                "provider": provider,
                "model": model,
                "tokens": normalize_usage(usage),
                "latency_ms": latency_ms,
            }
        )
    except Exception:
        pass
