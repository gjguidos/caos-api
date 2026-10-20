from typing import Any

from pydantic import BaseModel


class RuleSpec(BaseModel):
    id: str
    description: str
    severity: str  # "info" | "warn" | "error"
    applies_to: str = "all"  # "headline" | "body" | "cta" | "all" etc.
    domain: str | None = None  # "green_claims", "gdpr", etc
    pack_id: str | None = None  # "green_claims_compliance"
    tags: list[str] = []  # ["legal", "privacy", "sustainability"]


class RulePackSpec(BaseModel):
    """
    Data-only description of a rule pack.
    Can live in code, DB, or both.
    """

    name: str  # "copywriter-basics"
    label: str  # "Copywriter basic rules"
    domain: str | None = None  # "copywriting", "management", "frontend", ...

    # Optional scoping: which roles this pack is “designed for”
    roles: list[str] = []  # ["writer", "reviewer", "auditor"]

    rules: list[RuleSpec]

    # Optional FE-friendly metadata
    summary: str | None = None
    description: str | None = None
    emoji: str | None = None  # "📏"
    icon: str | None = None  # "lucide:check-circle"
    tags: list[str] = []
    badges: list[str] = []  # ["beta", "internal", "strict"]

    # Optional config knobs: pack-level options
    config_schema: dict[str, Any] = {}
    default_config: dict[str, Any] = {}

    # Versioning / lifecycle
    version: str = "1.0.0"
    deprecated: bool = False
