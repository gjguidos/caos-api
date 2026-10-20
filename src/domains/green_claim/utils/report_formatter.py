"""
Markdown report formatting utilities for green claim analysis.

Converts structured GreenClaimResponse objects into a
well-formatted Markdown report.
"""

from src.domains.green_claim.entities.green_claim_models import (
    GreenClaimReportItem,
    GreenClaimResponse,
    ProblematicWordResult,
)


def _format_executive_summary(
    report_items: list[GreenClaimReportItem], problematic_words: ProblematicWordResult
) -> list[str]:
    total_claims = len(report_items)
    total_violations = sum(len(item.principles_violated) for item in report_items)
    total_problematic_words = problematic_words.total_count

    return [
        "## Executive Summary",
        "",
        "| Metric | Value |",
        "|-------|-------|",
        f"| Total Green Claims Identified | {total_claims} |",
        f"| Total Principle Violations | {total_violations} |",
        f"| Problematic Words Detected | {total_problematic_words} |",
    ]


def _format_claim_item(
    claim_item: GreenClaimReportItem,
    idx: int,
) -> list[str]:
    lines = [
        f"### Claim {idx}",
        "",
        "**Statement:**",
        "",
        f"> {claim_item.statement}",
        "",
    ]

    if not claim_item.principles_violated:
        lines.extend(
            [
                "**Status:** ✅ Compliant",
                "",
                "_No principle violations detected._",
                "",
            ]
        )
        return lines

    lines.extend(
        [
            "**Status:** ❌ Non-compliant",
            "",
            f"**Violations Found:** {len(claim_item.principles_violated)}",
            "",
        ]
    )

    for violation in claim_item.principles_violated:
        lines.extend(
            [
                f"- **Principle:** {violation.principle}",
                f"  - **Confidence:** {violation.confidence.upper()}",
                f"  - **Explanation:** {violation.explanation}",
                f"  - **Guidelines:** {', '.join(violation.guidelines_reference)}",
                f"  - **Recommendation:** {violation.improvement_recommendation}",
                "",
            ]
        )

    return lines


def _format_problematic_words(
    problematic_words: ProblematicWordResult,
) -> list[str]:
    if problematic_words.total_count == 0:
        return []

    lines = [
        "## Problematic Words Detected",
        "",
        f"**Total Instances:** {problematic_words.total_count}",
        "",
    ]

    if problematic_words.exact_matches:
        lines.append(f"### Exact Matches ({len(problematic_words.exact_matches)})")
        lines.append("")
        for match in problematic_words.exact_matches:
            lines.append(f'- **"{match.text}"** ')
        lines.append("")

    if problematic_words.close_matches:
        lines.append(f"### Close Matches ({len(problematic_words.close_matches)})")
        lines.append("")
        for match in problematic_words.close_matches:
            lines.append(
                f'- **"{match.text}"** '
                f"(similar to: `{match.matched_against}`, "
                f"confidence: {match.confidence:.2f}, "
            )
        lines.append("")

    return lines


def format_green_claim_as_markdown(response: GreenClaimResponse) -> str:
    """Format a GreenClaimResponse into a Markdown report."""
    lines = [
        "# Green Claim Analysis Report",
        "",
    ]

    # Executive Summary
    lines.extend(_format_executive_summary(response.green_claim_report, response.problematic_words))

    # Detailed Analysis
    lines.append("## Detailed Analysis")
    lines.append("")

    if response.green_claim_report:
        for idx, claim_item in enumerate(response.green_claim_report, start=1):
            lines.extend(_format_claim_item(claim_item, idx))
    else:
        lines.append("_No green claims identified in the analyzed content._")
        lines.append("")

    # Problematic Words
    lines.extend(_format_problematic_words(response.problematic_words))

    # Legal Statement
    lines.extend(
        [
            "## Legal Statement",
            "",
            response.legal_statement,
            "",
        ]
    )

    # Guidelines Reference
    lines.extend(
        [
            "## Guidelines Reference",
            "",
            "For more information, consult:",
            "",
            f"- {response.guidelines}",
            "",
        ]
    )

    return "\n".join(lines)
