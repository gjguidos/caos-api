from __future__ import annotations

from contextlib import contextmanager
from typing import Any


@contextmanager
def persona_flags(
    agent: Any,
    *,
    pipeline_neutral: bool | None = None,
    persona_mode: str | None = None,
    persona_name: str | None = None,
    persona_delta: str | None = None,
):
    prev = {
        "_pipeline_neutral": getattr(agent, "_pipeline_neutral", None),
        "_chat_persona_mode": getattr(agent, "_chat_persona_mode", None),
        "_chat_persona_name": getattr(agent, "_chat_persona_name", None),
        "_chat_persona_delta": getattr(agent, "_chat_persona_delta", None),
    }
    try:
        if pipeline_neutral is not None:
            setattr(agent, "_pipeline_neutral", bool(pipeline_neutral))
        if persona_mode is not None:
            setattr(agent, "_chat_persona_mode", persona_mode)
        if persona_name is not None:
            setattr(agent, "_chat_persona_name", str(persona_name))
        if persona_delta is not None:
            setattr(agent, "_chat_persona_delta", str(persona_delta))
        yield
    finally:
        for k, v in prev.items():
            if v is None:
                try:
                    delattr(agent, k)
                except Exception:
                    pass
            else:
                setattr(agent, k, v)
