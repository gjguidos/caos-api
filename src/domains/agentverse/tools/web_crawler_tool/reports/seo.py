from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

SEO_SCORE_MIN = 0.0
SEO_SCORE_MAX = 1.0

SEO_GRADE_THRESHOLDS: dict[str, float] = {
    "excellent": 0.85,
    "good": 0.65,
    "partial": 0.40,
    "poor": 0.0,
}

SEO_SCORE_WEIGHTS: dict[str, float] = {
    "title": 0.20,
    "description": 0.25,
    "canonical": 0.15,
    "open_graph": 0.15,
    "twitter": 0.10,
    "json_ld": 0.15,
}

SEO_SCORE_PENALTIES: dict[str, float] = {
    "error": 0.15,
    "warning": 0.07,
    "info": 0.0,
}

class SEOIssue(BaseModel):
    severity: str = "info"  # info | warning | error
    code: str
    message: str
    field: str | None = None


class SEOReport(BaseModel):
    page_url: str
    title: str = ""

    description: str | None = None
    canonical: str | None = None

    open_graph: dict[str, Any] = Field(default_factory=dict)
    twitter: dict[str, Any] = Field(default_factory=dict)
    json_ld_count: int = 0

    has_title: bool = False
    has_description: bool = False
    has_canonical: bool = False
    has_open_graph: bool = False
    has_twitter: bool = False
    has_json_ld: bool = False

    title_len: int = 0
    description_len: int = 0

    score: float = 0.0
    grade: str = "unknown"

    issues: list[SEOIssue] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def build_seo_report(
    *,
    page_url: str,
    title: str = "",
    html_report: dict[str, Any] | None = None,
) -> SEOReport:
    html_report = html_report or {}
    seo = html_report.get("seo") or {}

    page_meta = html_report.get("page") or {}

    title = _clean_string(title) or _clean_string(page_meta.get("title"))
    description = _clean_optional_string(seo.get("description"))
    canonical = _clean_optional_string(seo.get("canonical"))

    open_graph = _normalize_metadata_dict(seo.get("open_graph"))
    twitter = _normalize_metadata_dict(seo.get("twitter"))
    json_ld_count = _safe_int(seo.get("json_ld_count"))

    has_title = bool(title)
    has_description = bool(description)
    has_canonical = bool(canonical)
    has_open_graph = bool(open_graph)
    has_twitter = bool(twitter)
    has_json_ld = json_ld_count > 0

    issues = _build_issues(
        title=title,
        description=description,
        canonical=canonical,
        open_graph=open_graph,
        twitter=twitter,
        json_ld_count=json_ld_count,
    )

    score = _score_seo(
        has_title=has_title,
        has_description=has_description,
        has_canonical=has_canonical,
        has_open_graph=has_open_graph,
        has_twitter=has_twitter,
        has_json_ld=has_json_ld,
        issues=issues,
    )

    return SEOReport(
        page_url=page_url,
        title=title,
        description=description,
        canonical=canonical,
        open_graph=open_graph,
        twitter=twitter,
        json_ld_count=json_ld_count,
        has_title=has_title,
        has_description=has_description,
        has_canonical=has_canonical,
        has_open_graph=has_open_graph,
        has_twitter=has_twitter,
        has_json_ld=has_json_ld,
        title_len=len(title),
        description_len=len(description or ""),
        score=score,
        grade=_grade(score),
        issues=issues,
        metadata={
            "source": "html_report.seo",
            "score": {
                "scale": {
                    "min": SEO_SCORE_MIN,
                    "max": SEO_SCORE_MAX,
                    "display": "0.00-1.00",
                    "percentage_equivalent": True,
                },
                "weights": SEO_SCORE_WEIGHTS,
                "penalties": SEO_SCORE_PENALTIES,
                "grade_thresholds": SEO_GRADE_THRESHOLDS,
            },
        },
    )


def _build_issues(
    *,
    title: str,
    description: str | None,
    canonical: str | None,
    open_graph: dict[str, Any],
    twitter: dict[str, Any],
    json_ld_count: int,
) -> list[SEOIssue]:
    issues: list[SEOIssue] = []

    if not title:
        issues.append(
            SEOIssue(
                severity="error",
                code="missing_title",
                field="title",
                message="Page title is missing.",
            )
        )
    elif len(title) < 10:
        issues.append(
            SEOIssue(
                severity="warning",
                code="short_title",
                field="title",
                message="Page title is very short.",
            )
        )
    elif len(title) > 70:
        issues.append(
            SEOIssue(
                severity="warning",
                code="long_title",
                field="title",
                message="Page title may be too long for search result display.",
            )
        )

    if not description:
        issues.append(
            SEOIssue(
                severity="error",
                code="missing_description",
                field="description",
                message="Meta description is missing.",
            )
        )
    elif len(description) < 50:
        issues.append(
            SEOIssue(
                severity="warning",
                code="short_description",
                field="description",
                message="Meta description is very short.",
            )
        )
    elif len(description) > 180:
        issues.append(
            SEOIssue(
                severity="warning",
                code="long_description",
                field="description",
                message="Meta description may be too long for search result display.",
            )
        )

    if not canonical:
        issues.append(
            SEOIssue(
                severity="warning",
                code="missing_canonical",
                field="canonical",
                message="Canonical URL is missing.",
            )
        )

    if not open_graph:
        issues.append(
            SEOIssue(
                severity="info",
                code="missing_open_graph",
                field="open_graph",
                message="Open Graph metadata is missing or was not detected.",
            )
        )

    if not twitter:
        issues.append(
            SEOIssue(
                severity="info",
                code="missing_twitter_card",
                field="twitter",
                message="Twitter/X card metadata is missing or was not detected.",
            )
        )

    if json_ld_count <= 0:
        issues.append(
            SEOIssue(
                severity="info",
                code="missing_json_ld",
                field="json_ld_count",
                message="No JSON-LD structured data blocks were detected.",
            )
        )

    return issues


def _score_seo(
    *,
    has_title: bool,
    has_description: bool,
    has_canonical: bool,
    has_open_graph: bool,
    has_twitter: bool,
    has_json_ld: bool,
    issues: list[SEOIssue],
) -> float:
    score = 0.0

    if has_title:
        score += SEO_SCORE_WEIGHTS["title"]

    if has_description:
        score += SEO_SCORE_WEIGHTS["description"]

    if has_canonical:
        score += SEO_SCORE_WEIGHTS["canonical"]

    if has_open_graph:
        score += SEO_SCORE_WEIGHTS["open_graph"]

    if has_twitter:
        score += SEO_SCORE_WEIGHTS["twitter"]

    if has_json_ld:
        score += SEO_SCORE_WEIGHTS["json_ld"]

    for issue in issues:
        score -= float(SEO_SCORE_PENALTIES.get(issue.severity, 0.0))
    return round(max(0.0, min(1.0, score)), 4)


def _grade(score: float) -> str:
    if score >= 0.85:
        return "excellent"

    if score >= 0.65:
        return "good"

    if score >= 0.40:
        return "partial"

    return "poor"


def _clean_string(value: Any) -> str:
    if value is None:
        return ""

    return " ".join(str(value).split()).strip()


def _clean_optional_string(value: Any) -> str | None:
    cleaned = _clean_string(value)
    return cleaned or None


def _normalize_metadata_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    return {
        str(key): item
        for key, item in value.items()
        if key is not None and item not in (None, "")
    }


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0