from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.domains.agentverse.tools.web_crawler_tool.types import (
    CrawledPage,
)


@dataclass
class PageContribution:
    increments: dict[str, int] = field(
        default_factory=dict
    )
    add_to_sets: dict[str, list[Any]] = field(
        default_factory=dict
    )
    media_items: list[dict[str, Any]] = field(
        default_factory=list
    )

    def increment(
        self,
        path: str,
        value: int = 1,
    ) -> None:
        self.increments[path] = (
            self.increments.get(path, 0) + value
        )

    def add_to_set(
        self,
        path: str,
        value: Any,
    ) -> None:
        if value is None:
            return

        self.add_to_sets.setdefault(
            path,
            [],
        ).append(value)


def build_page_contribution(
    page: CrawledPage,
) -> PageContribution:
    contribution = PageContribution()

    reports = page.reports or {}

    contribution.increment("page_totals.page_count")

    if page.error:
        contribution.increment("page_totals.failed_pages")
    else:
        contribution.increment("page_totals.successful_pages")

    status_code = str(page.status_code or "unknown")
    content_type = page.content_type or "unknown"

    contribution.increment(
        f"page_totals.status_codes.{status_code}"
    )
    contribution.increment(
        f"page_totals.content_types.{content_type}"
    )

    _add_report_contribution(
        contribution,
        reports,
        report_name="links",
        counters=[
            "total_links",
            "unique_links",
            "internal_links",
            "external_links",
            "download_links",
            "social_links",
            "cta_candidates",
        ],
        sets=["unique_domains"],
    )

    _add_report_contribution(
        contribution,
        reports,
        report_name="forms",
        counters=[
            "form_count",
            "total_controls",
            "required_controls",
            "unlabeled_controls",
            "sensitive_controls",
            "search_forms",
            "auth_forms",
            "newsletter_forms",
            "contact_forms",
            "external_action_forms",
        ],
    )

    _add_report_contribution(
        contribution,
        reports,
        report_name="content_blocks",
        counters=[
            "block_count",
            "total_text_len",
            "primary_evidence_blocks",
            "supporting_evidence_blocks",
            "text_heavy_blocks",
        ],
    )

    _add_report_contribution(
        contribution,
        reports,
        report_name="detected_html_blocks",
        counters=[
            "block_count",
            "hero_blocks",
            "navigation_blocks",
            "cta_blocks",
            "card_blocks",
            "media_blocks",
            "footer_blocks",
            "article_blocks",
        ],
    )

    media = reports.get("media") or {}
    if media:
        contribution.increment(
            "media.pages_with_media"
        )

        _add_report_contribution(
            contribution,
            reports,
            report_name="media",
            counters=[
                "total_media",
                "unique_media",
                "image_count",
                "video_count",
                "iframe_count",
                "background_image_count",
                "external_media_count",
                "hero_candidates",
                "logo_candidates",
                "product_candidates",
            ],
            sets=["unique_domains"],
        )

    documents = reports.get("documents") or {}
    if documents:
        _add_report_contribution(
            contribution,
            reports,
            report_name="documents",
            counters=[
                "document_count",
                "pdf_count",
                "word_count",
                "spreadsheet_count",
                "presentation_count",
                "text_count",
                "data_count",
                "internal_documents",
                "external_documents",
            ],
            sets=["unique_domains"],
        )

    seo = reports.get("seo") or {}
    if seo:
        contribution.increment("seo.page_count")

        for field, counter in (
            ("has_title", "pages_with_title"),
            ("has_description", "pages_with_description"),
            ("has_canonical", "pages_with_canonical"),
            ("has_open_graph", "pages_with_open_graph"),
            ("has_twitter", "pages_with_twitter"),
            ("has_json_ld", "pages_with_json_ld"),
        ):
            if seo.get(field):
                contribution.increment(
                    f"seo.{counter}"
                )

        score = seo.get("score")
        if score is not None:
            contribution.increment(
                "seo.score_sum",
                int(float(score) * 10_000),
            )
            contribution.increment(
                "seo.score_count"
            )

        grade = seo.get("grade")
        if grade:
            contribution.increment(
                f"seo.grades.{grade}"
            )

    return contribution


def _add_report_contribution(
    contribution: PageContribution,
    reports: dict[str, Any],
    *,
    report_name: str,
    counters: list[str],
    sets: list[str] | None = None,
) -> None:
    report = reports.get(report_name) or {}

    if not report:
        return

    for key in counters:
        value = report.get(key)

        if isinstance(value, bool):
            value = int(value)

        if isinstance(value, int):
            contribution.increment(
                f"reports.{report_name}.{key}",
                value,
            )

    for key in sets or []:
        values = report.get(key) or []

        if not isinstance(values, list):
            continue

        for value in values:
            contribution.add_to_set(
                f"reports.{report_name}.{key}",
                value,
            )