from __future__ import annotations

from typing import Any

from src.domains.agentverse.tools.web_crawler_tool.security.verdict import (
    SecurityVerdict,
)
from src.domains.agentverse.tools.web_crawler_tool.types import SecurityAuditEntry


def audit_from_verdict(
    *,
    url: str,
    stage: str,
    verdict: SecurityVerdict,
    details: dict[str, Any] | None = None,
) -> SecurityAuditEntry:
    return SecurityAuditEntry(
        url=url,
        decision=verdict.decision,
        stage=stage,
        reason=verdict.reason,
        category=verdict.category,
        rule_id=verdict.rule_id,
        details={
            **(verdict.details or {}),
            **(details or {}),
        },
    )


def audit_block(
    *,
    url: str,
    stage: str,
    reason: str,
    category: str = "policy",
    rule_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> SecurityAuditEntry:
    return SecurityAuditEntry(
        url=url,
        decision="block",
        stage=stage,
        reason=reason,
        category=category,
        rule_id=rule_id,
        details=details or {},
    )


def audit_skip(
    *,
    url: str,
    stage: str,
    reason: str,
    category: str = "crawl_scope",
    rule_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> SecurityAuditEntry:
    return SecurityAuditEntry(
        url=url,
        decision="skip",
        stage=stage,
        reason=reason,
        category=category,
        rule_id=rule_id,
        details=details or {},
    )


def audit_error(
    *,
    url: str,
    stage: str,
    reason: str,
    category: str = "fetch_error",
    rule_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> SecurityAuditEntry:
    return SecurityAuditEntry(
        url=url,
        decision="error",
        stage=stage,
        reason=reason,
        category=category,
        rule_id=rule_id,
        details=details or {},
    )