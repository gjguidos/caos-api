from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup

from src.domains.agentverse.tools.web_crawler_tool.helpers.html_report import (
    build_html_components_report,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.content_blocks import (
    build_content_blocks_report,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.detected_html_blocks import (
    build_detected_html_blocks_report,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.document_inventory import (
    build_document_inventory_report,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.form_inventory import (
    build_form_inventory_report,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.link_inventory import (
    build_link_inventory_report,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.media_inventory import (
    build_page_media_inventory_report,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.metadata_utils import (
    build_page_report_summary,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.seo import (
    build_seo_report,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.snapshot import (
    build_snapshot_report,
)


def build_page_reports(
    *,
    html: str,
    url: str,
    final_url: str,
    title: str,
    text: str,
    links: list[str],
    status_code: int | None,
    content_type: str | None,
    diagnostics: dict[str, Any] | None = None,
    requested_reports: set[str] | list[str] | None = None,
    max_blocks: int = 80,
    max_text_chars_per_block: int = 1200,
    min_text_len: int = 80,
    raw_soup: BeautifulSoup | None = None,
    clean_soup: BeautifulSoup | None = None,
    reports_requested: set[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    requested = (
        requested_reports
        if requested_reports is not None
        else reports_requested
    )

    normalized_reports = (
        None
        if requested is None
        else {
            str(item).strip().lower()
            for item in requested
            if str(item).strip()
        }
    )

    def wants(name: str) -> bool:
        return (
            normalized_reports is None
            or "all" in normalized_reports
            or name in normalized_reports
        )

    needs_html_components = any(
        wants(name)
        for name in (
            "html_components",
            "media",
            "asset_light",
            "links",
            "forms",
            "seo",
        )
    )

    if raw_soup is None and needs_html_components:
        raw_soup = BeautifulSoup(html or "", "lxml")

    needs_clean_soup = any(
        wants(name)
        for name in (
            "content_blocks",
            "detected_html_blocks",
            "documents",
        )
    )

    if clean_soup is None and needs_clean_soup:
        clean_soup = BeautifulSoup(html or "", "lxml")

        for element in clean_soup(
            ["script", "style", "noscript", "svg"]
        ):
            element.decompose()

    html_components = (
        build_html_components_report(
            html=html,
            url=url,
            final_url=final_url,
            soup=raw_soup,
        )
        if needs_html_components
        else {}
    )

    reports: dict[str, Any] = {}

    if wants("html_components"):
        reports["html_components"] = html_components

    if wants("media"):
        reports["media"] = (
            build_page_media_inventory_report(
                html=html,
                page_url=final_url,
                html_report=html_components,
                soup=raw_soup,
            ).model_dump()
        )

    if wants("links") or wants("documents"):
        reports["links"] = (
            build_link_inventory_report(
                page_url=final_url,
                links=links,
                html_report=html_components,
            ).model_dump()
        )

    if wants("forms"):
        reports["forms"] = (
            build_form_inventory_report(
                page_url=final_url,
                html_report=html_components,
            ).model_dump()
        )

    if wants("seo"):
        reports["seo"] = (
            build_seo_report(
                page_url=final_url,
                title=title,
                html_report=html_components,
            ).model_dump()
        )

    if wants("content_blocks"):
        reports["content_blocks"] = (
            build_content_blocks_report(
                html=html,
                page_url=final_url,
                max_blocks=max_blocks,
                max_text_chars_per_block=max_text_chars_per_block,
                min_text_len=min_text_len,
                clean_soup=clean_soup,
            ).model_dump()
        )

    if wants("detected_html_blocks") or wants("documents"):
        reports["detected_html_blocks"] = (
            build_detected_html_blocks_report(
                html=html,
                page_url=final_url,
                max_blocks=max_blocks,
                max_text_chars_per_block=max_text_chars_per_block,
                clean_soup=clean_soup,
            ).model_dump()
        )

    if wants("documents"):
        reports["documents"] = (
            build_document_inventory_report(
                page_url=final_url,
                links_report=reports.get("links", {}),
                content_blocks_report=reports.get(
                    "detected_html_blocks",
                    {},
                ),
            ).model_dump()
        )

    page_payload = {
        "url": url,
        "final_url": final_url,
        "status_code": status_code,
        "content_type": content_type,
        "title": title,
        "text": text,
        "links": links,
        "html_report": html_components,
        "diagnostics": diagnostics or {},
        "reports": reports,
    }

    if wants("snapshot"):
        reports["snapshot"] = (
            build_snapshot_report(
                page=page_payload,
            ).model_dump()
        )

    reports["report_summary"] = (
        build_page_report_summary(
            url=url,
            final_url=final_url,
            status_code=status_code,
            content_type=content_type,
            title=title,
            reports=reports,
        )
    )

    return html_components, reports