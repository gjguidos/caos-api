"""Human-readable rendering of a ``caos.nataraja.report.v1`` (the JSON is canonical)."""

from __future__ import annotations

import json
from typing import Any

_MARK = {"passed": "✓", "warning": "!", "failed": "✗", "blocked": "■", "deferred": "…"}


def to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True, default=str) + "\n"


def to_text(report: dict[str, Any]) -> str:
    lines = [
        f"NATARAJA {report['mode'].upper()} — {report['environment']}: {report['status'].upper()}",
    ]
    revisions = report.get("revisions") or {}
    labels = (
        ("manifest_revision", "manifest r"),
        ("manifest_digest", "manifest "),
        ("policy_version", "policy "),
        ("installed_policy_version", "installed "),
    )
    parts = [f"{label}{str(revisions[key])[:12]}" for key, label in labels if revisions.get(key)]
    if parts:
        lines.append(" · ".join(parts))
    lines.append("")
    lines.append("Resources")
    for item in report["resources"]:
        lines.append(
            f"  {_MARK[item['status']]} {item['kind']:<15} {item['name']:<32} {item['status']}"
        )
    if report["findings"]:
        lines.append("")
        lines.append("Findings")
        for item in report["findings"]:
            lines.append(
                f"  {_MARK[item['status']]} [{item['code']}] {item['resource']}: {item['message']}"
            )
    if report["reconciliation_plan"]:
        lines.append("")
        lines.append("Reconciliation plan (dry run — nothing executed)")
        for index, item in enumerate(report["reconciliation_plan"], 1):
            flags = ", ".join(
                flag
                for flag, on in (
                    ("destructive", item["destructive"]),
                    ("human approval", item["requires_human_approval"]),
                )
                if on
            )
            lines.append(f"  {index}. {item['action']} ({item['classification']}; {flags})")
            lines.append(f"     resource: {item['resource']}")
            lines.append(f"     by: {item['responsible']}")
            lines.append(f"     authority: {item['authority']}")
            if item["depends_on"]:
                lines.append(f"     after: {', '.join(item['depends_on'])}")
    lines.append("")
    lines.append(f"report digest {report['report_digest'][:16]}")
    return "\n".join(lines) + "\n"
