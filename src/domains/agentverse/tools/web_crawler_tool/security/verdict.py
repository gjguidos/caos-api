from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SecurityDecision = Literal["allow", "block", "warn"]


class SecurityVerdict(BaseModel):
    decision: SecurityDecision
    reason: str
    category: str = "unknown"
    rule_id: str | None = None
    details: dict = Field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.decision in {"allow", "warn"}