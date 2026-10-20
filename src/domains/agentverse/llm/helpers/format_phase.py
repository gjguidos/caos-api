from __future__ import annotations

import inspect
import logging
from typing import Any

from src.base.integrations.mcp.proxy_contract import ToolEnv

logger = logging.getLogger(__name__)


def _parse_payload(result: Any, fallback_text: str) -> ToolEnv:
    """Read one `format_result` return into the canonical env.

    The wrapper shape is the one `ToolEnv.as_payload` emits, so `answer`,
    `addons` and `data` are read back into the model that produced them. An
    unusable or blank `answer` falls back to the text the tool was given without
    discarding whatever else it returned.
    """
    if not isinstance(result, dict):
        return ToolEnv(answer=str(result))

    payload = result.get("result") if isinstance(result.get("result"), dict) else {}
    answer = payload.get("answer")
    addons = payload.get("addons")
    data = payload.get("data")

    return ToolEnv(
        answer=answer.strip() if isinstance(answer, str) and answer.strip() else fallback_text,
        addons=addons if isinstance(addons, dict) else {},
        data=data if isinstance(data, dict) else None,
    )


async def _call_format_result(tool: Any, text: str) -> ToolEnv | None:
    """One tool's `format_result`, or `None` if it did not produce an env.

    A tool without a callable `format_result`, or one that raises, leaves the
    turn's text untouched rather than blanking it.
    """
    formatter = getattr(tool, "format_result", None)
    if not callable(formatter):
        return None

    try:
        result = formatter(text)
        if inspect.isawaitable(result):
            result = await result
    except Exception:
        logger.exception("format_result failed for tool=%s", getattr(tool, "key", tool))
        return None

    logger.info(
        "format_result applied by tool=%s (%s)",
        getattr(tool, "key", tool),
        type(tool).__name__,
    )
    return _parse_payload(result, fallback_text=text)


def _merge_data(
    base: dict[str, Any] | None, incoming: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Accumulate `data` across formatters, keying on presence not truthiness.

    `as_env` emits `data` whenever it is not `None`, so an explicit empty payload
    has to stay distinguishable from a tool that returned none at all.
    """
    if incoming is None:
        return base
    return {**(base or {}), **incoming}


async def format_step(*, tools: list[Any], raw_text: str) -> ToolEnv:
    """Run the turn's formatters, chaining each one's text into the next.

    Returns the canonical env so `invoker.llm` hands the workflow engine the
    same `ToolEnv.as_env()` keys the pure path produces - declared output
    `path`s (`answer`, `data.<field>`) resolve identically in both modes.
    """
    env = ToolEnv(answer=raw_text or "")

    for tool in tools or []:
        step = await _call_format_result(tool, env.answer)
        if step is None:
            continue

        env = ToolEnv(
            answer=step.answer or env.answer,
            addons={**env.addons, **step.addons},
            data=_merge_data(env.data, step.data),
        )

    return env
