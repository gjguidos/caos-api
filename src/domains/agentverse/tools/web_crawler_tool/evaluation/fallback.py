from __future__ import annotations

from dataclasses import dataclass, field

from src.domains.agentverse.tools.web_crawler_tool.evaluation.quality import (
    PageQualityReport,
    evaluate_page_quality,
)
from src.domains.agentverse.tools.web_crawler_tool.types import CrawledPage


@dataclass(slots=True)
class BrowserFallbackReport:
    should_fallback: bool
    score: int
    reasons: list[str] = field(default_factory=list)
    quality: dict = field(default_factory=dict)

    def model_dump(self) -> dict:
        return {
            "should_fallback": self.should_fallback,
            "score": self.score,
            "reasons": self.reasons,
            "quality": self.quality,
        }


def evaluate_browser_fallback(
    page: CrawledPage,
    *,
    min_text_chars: int = 300,
    min_links: int = 1,
    min_headings: int = 1,
    score_threshold: int = 5,
) -> BrowserFallbackReport:
    quality: PageQualityReport = evaluate_page_quality(
        page,
        min_text_chars=min_text_chars,
        min_links=min_links,
        min_headings=min_headings,
    )

    diagnostics = page.diagnostics or {}
    text_len = int(quality.signals.get("text_len") or 0)
    heading_count = int(quality.signals.get("heading_count") or 0)
    script_count = int(quality.signals.get("script_count") or 0)
    html_size = int(quality.signals.get("html_size") or 0)

    score = 0
    reasons: list[str] = []

    if page.error:
        score += 5
        reasons.append(f"static_fetch_error:{page.error}")

    if quality.grade == "empty":
        score += 5
        reasons.append("no_meaningful_html_structure")

    if quality.grade == "weak":
        score += 3
        reasons.append("weak_static_extraction")

    if diagnostics.get("has_js_required_marker"):
        score += 5
        reasons.append("javascript_required_marker")

    if diagnostics.get("has_spa_marker") and text_len < min_text_chars:
        score += 4
        reasons.append("spa_shell_marker_with_weak_text")

    if diagnostics.get("has_framework_marker") and text_len < min_text_chars and heading_count < min_headings:
        score += 3
        reasons.append("framework_marker_with_weak_content")

    if script_count >= 5 and text_len < min_text_chars:
        score += 3
        reasons.append("high_script_low_text_ratio")

    if html_size > 50_000 and text_len < min_text_chars:
        score += 2
        reasons.append("large_html_low_visible_text")

    if diagnostics.get("has_placeholder_marker") and text_len < min_text_chars:
        score += 3
        reasons.append("placeholder_or_loading_shell")

    if diagnostics.get("has_protection_marker"):
        score += 2
        reasons.append("protection_or_interstitial_marker")

    should_fallback = score >= score_threshold

    return BrowserFallbackReport(
        should_fallback=should_fallback,
        score=score,
        reasons=reasons or ["static_result_sufficient"],
        quality=quality.model_dump(),
    )