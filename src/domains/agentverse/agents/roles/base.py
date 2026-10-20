from __future__ import annotations

import re
from typing import Any, Protocol

from pydantic import BaseModel


# ---------- FE-visible role tool declaration ----------
class RoleToolRef(BaseModel):
    key: str
    name: str | None = None
    version: str | None = None
    config: dict[str, Any] = {}
    required: bool = False
    hidden: bool = False  # BE injects even if FE hides


QUESTION_RE = re.compile(r"\?$|^\s*(who|what|where|when|why|how|which|name)\b", re.I)


def note(ctx: dict, kind: str, **fields):
    ctx.setdefault("rapport_trace", []).append({"kind": kind, **fields})


def dedup(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen and x:
            seen.add(x)
            out.append(x)

    return out


# ---------- RoleSpec ----------
class RoleSpec(Protocol):
    name: str
    default_rubric: dict[str, float]
    max_refine_loops: int  # orchestration hint

    # Role-scoped defaults
    @classmethod
    def default_tools(cls) -> list[RoleToolRef]: ...

    # Policies
    async def analyze(self, ctx: dict[str, Any]) -> dict[str, Any]: ...
    async def plan(self, analysis: dict[str, Any]) -> dict[str, Any]: ...
    async def task(self, plan: dict[str, Any], tools: Any) -> dict[str, Any]: ...
    async def refine(self, draft: dict[str, Any], tools: Any) -> dict[str, Any]: ...
    async def evaluate(self, output: dict[str, Any], rubric: dict[str, Any]) -> dict[str, Any]: ...
