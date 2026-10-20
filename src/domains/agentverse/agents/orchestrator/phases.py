from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


class Phase(str, Enum):
    ANALYZE = "analyze"
    PLAN = "plan"
    TASK = "task"
    REFINE = "refine"
    EVALUATE = "evaluate"


@dataclass
class SuccessCriteria:
    must_have: list[str] = field(default_factory=list)  # hard constraints
    prefer: list[str] = field(default_factory=list)  # soft goals
    quality_threshold: float = 0.0  # 0..1 accept cutoff


@dataclass
class EvalResult:
    pass_fail: bool = False
    score: float = 0.0
    rubric: dict[str, int] = field(default_factory=dict)  # e.g. {"adherence":4,"clarity":5}
    comments: dict[str, str] = field(default_factory=dict)  # targeted suggestions
    assumptions_used: list[str] = field(default_factory=list)


@dataclass
class Artifact:
    kind: Literal["text", "code", "plan", "analysis", "other"] = "text"
    content: Any = ""
    meta: dict[str, Any] = field(default_factory=dict)  # word_count, grade_level, etc.
