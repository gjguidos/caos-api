from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from src.domains.agentverse.tools.web_crawler_tool.types import CrawledPage


QualityGrade = Literal["empty", "weak", "usable", "rich"]


@dataclass(slots=True)
class PageQualityReport:
    grade: QualityGrade
    score: int
    reasons: list[str] = field(default_factory=list)
    signals: dict = field(default_factory=dict)

    def model_dump(self) -> dict:
        return {
            "grade": self.grade,
            "score": self.score,
            "reasons": self.reasons,
            "signals": self.signals,
        }


def evaluate_page_quality(
    page: CrawledPage,
    *,
    min_text_chars: int = 300,
    min_links: int = 1,
    min_headings: int = 1,
) -> PageQualityReport:
    text = (page.text or "").strip()
    links = page.links or []
    report = page.html_report or {}
    diagnostics = page.diagnostics or {}

    headings = report.get("headings") or []
    semantic_regions = report.get("semantic_regions") or {}
    interactive = report.get("interactive") or {}
    media = report.get("media") or {}
    seo = report.get("seo") or {}

    semantic_count = _sum_int_values(semantic_regions)
    link_count = len(links) or int(interactive.get("link_count") or 0)
    button_count = int(interactive.get("button_count") or 0)
    image_count = int(media.get("image_count") or 0)
    script_count = int(diagnostics.get("script_count") or 0)
    html_size = int(diagnostics.get("html_size") or 0)

    title_present = bool(page.title)
    text_len = len(text)
    heading_count = len(headings)
    seo_description_present = bool(seo.get("description"))

    score = 0
    reasons: list[str] = []

    if page.error:
        score -= 5
        reasons.append(f"page_error:{page.error}")

    if title_present:
        score += 2
        reasons.append("title_present")

    if text_len >= min_text_chars:
        score += 3
        reasons.append("meaningful_text_present")
    elif text_len > 0:
        score += 1
        reasons.append("small_text_present")

    if heading_count >= min_headings:
        score += 2
        reasons.append("headings_present")

    if semantic_count > 0:
        score += 2
        reasons.append("semantic_regions_present")

    if link_count >= min_links:
        score += 1
        reasons.append("links_present")

    if button_count > 0:
        score += 1
        reasons.append("buttons_present")

    if image_count > 0:
        score += 1
        reasons.append("media_present")

    if seo_description_present:
        score += 1
        reasons.append("seo_description_present")

    if script_count >= 5 and text_len < min_text_chars:
        score -= 2
        reasons.append("high_script_low_text")

    if html_size > 50_000 and text_len < min_text_chars:
        score -= 2
        reasons.append("large_html_low_visible_text")

    if diagnostics.get("has_js_required_marker"):
        score -= 5
        reasons.append("javascript_required_marker")

    if diagnostics.get("has_placeholder_marker") and text_len < min_text_chars:
        score -= 3
        reasons.append("placeholder_or_loading_shell")

    if diagnostics.get("has_protection_marker"):
        score -= 2
        reasons.append("protection_or_interstitial_marker")

    if not reasons:
        reasons.append("no_quality_signals")

    grade = _grade_from_score(
        score=score,
        title_present=title_present,
        text_len=text_len,
        heading_count=heading_count,
        link_count=link_count,
        semantic_count=semantic_count,
    )

    return PageQualityReport(
        grade=grade,
        score=score,
        reasons=reasons,
        signals={
            "title_present": title_present,
            "text_len": text_len,
            "heading_count": heading_count,
            "semantic_count": semantic_count,
            "link_count": link_count,
            "button_count": button_count,
            "image_count": image_count,
            "script_count": script_count,
            "html_size": html_size,
            "seo_description_present": seo_description_present,
        },
    )


def has_meaningful_html_structure(
    page: CrawledPage,
    *,
    min_text_chars: int = 300,
    min_links: int = 1,
    min_headings: int = 1,
) -> bool:
    quality = evaluate_page_quality(
        page,
        min_text_chars=min_text_chars,
        min_links=min_links,
        min_headings=min_headings,
    )

    return quality.grade in {"usable", "rich"}


def _grade_from_score(
    *,
    score: int,
    title_present: bool,
    text_len: int,
    heading_count: int,
    link_count: int,
    semantic_count: int,
) -> QualityGrade:
    if not any([title_present, text_len, heading_count, link_count, semantic_count]):
        return "empty"

    if score <= 1:
        return "weak"

    if score <= 6:
        return "usable"

    return "rich"


def _sum_int_values(value: dict) -> int:
    total = 0

    for item in value.values():
        if isinstance(item, int):
            total += item

    return total