from __future__ import annotations

from typing import Any

from src.domains.agentverse.tools.web_crawler_tool.types import (
    CrawlerCoverageMetadata,
)


def build_crawl_summary_report(
    *,
    pages: list[dict[str, Any]],
    reports: dict[str, Any],
    matches: list[dict[str, Any]] | None = None,
    errors: list[str] | None = None,
    security_audit: list[dict[str, Any]] | None = None,
    coverage: CrawlerCoverageMetadata | None = None,
) -> dict[str, Any]:
    matches = matches or []
    errors = errors or []
    security_audit = security_audit or []
    coverage = (
        coverage
        or CrawlerCoverageMetadata()
    )

    successful_pages = [
        page for page in pages
        if not page.get("error")
    ]

    failed_pages = [
        page for page in pages
        if page.get("error")
    ]

    status_codes: dict[str, int] = {}
    content_types: dict[str, int] = {}

    for page in pages:
        status_code = page.get("status_code")
        content_type = page.get("content_type") or "unknown"

        status_key = str(status_code) if status_code is not None else "unknown"
        status_codes[status_key] = status_codes.get(status_key, 0) + 1
        content_types[content_type] = content_types.get(content_type, 0) + 1

    summary: dict[str, Any] = {
        "contract_version": "crawler.crawl_summary.v1",
        "page_totals": {
            "page_count": len(pages),
            "successful_pages": len(successful_pages),
            "failed_pages": len(failed_pages),
            "status_codes": status_codes,
            "content_types": content_types,
        },
        "coverage": {
            "checked": coverage.checked,
            "complete": coverage.complete,
            "reused_pages": coverage.reusable_count,
            "acquisition_candidates": (
                coverage.acquisition_candidates
            ),
            "planned_acquisition": (
                coverage.planned_acquisition_count
            ),
            "plan_truncated": (
                coverage.plan_truncated
            ),
        },
        "report_totals": {
            "available_reports": sorted(reports.keys()),
            "report_count": len(reports),
        },
        "match_totals": {
            "matches": len(matches),
        },
        "error_totals": {
            "errors": len(errors),
            "failed_pages": len(failed_pages),
            "security_audit_entries": len(security_audit),
            "blocked_security_events": sum(
                1 for item in security_audit
                if str(item.get("decision", "")).lower() in {"block", "blocked"}
            ),
        },
    }

    if "crawl_graph" in reports:
        crawl_graph = reports.get("crawl_graph") or {}
        summary["crawl_graph_totals"] = {
            "nodes": crawl_graph.get("node_count", 0),
            "edges": crawl_graph.get("edge_count", 0),
            "visited": crawl_graph.get("visited_count", 0),
            "queued": crawl_graph.get("queued_count", 0),
            "skipped": crawl_graph.get("skipped_count", 0),
            "blocked": crawl_graph.get("blocked_count", 0),
            "errors": crawl_graph.get("error_count", 0),
            "max_depth_seen": crawl_graph.get("max_depth_seen", 0),
            "internal_edges": crawl_graph.get("internal_edge_count", 0),
            "external_edges": crawl_graph.get("external_edge_count", 0),
            "duplicate_edges": crawl_graph.get("duplicate_edge_count", 0),
        }

    if "content_blocks" in reports:
        content_blocks = reports.get("content_blocks") or {}
        summary["content_totals"] = {
            "content_blocks": content_blocks.get("block_count", 0),
            "total_text_len": content_blocks.get("total_text_len", 0),
            "primary_evidence_blocks": content_blocks.get("primary_evidence_blocks", 0),
            "supporting_evidence_blocks": content_blocks.get("supporting_evidence_blocks", 0),
            "text_heavy_blocks": content_blocks.get("text_heavy_blocks", 0),
        }

    if "detected_html_blocks" in reports:
        detected_html_blocks = reports.get("detected_html_blocks") or {}
        summary["html_block_totals"] = {
            "detected_html_blocks": detected_html_blocks.get("block_count", 0),
            "hero_blocks": detected_html_blocks.get("hero_blocks", 0),
            "navigation_blocks": detected_html_blocks.get("navigation_blocks", 0),
            "cta_blocks": detected_html_blocks.get("cta_blocks", 0),
            "card_blocks": detected_html_blocks.get("card_blocks", 0),
            "media_blocks": detected_html_blocks.get("media_blocks", 0),
            "footer_blocks": detected_html_blocks.get("footer_blocks", 0),
            "article_blocks": detected_html_blocks.get("article_blocks", 0),
        }

    if "links" in reports:
        links = reports.get("links") or {}
        summary["link_totals"] = {
            "total_links": links.get("total_links", 0),
            "unique_links": links.get("unique_links", 0),
            "internal_links": links.get("internal_links", 0),
            "external_links": links.get("external_links", 0),
            "download_links": links.get("download_links", 0),
            "social_links": links.get("social_links", 0),
            "cta_candidates": links.get("cta_candidates", 0),
            "unique_domains": links.get("unique_domains", []),
        }

    if "documents" in reports:
        documents = reports.get("documents") or {}
        summary["document_totals"] = {
            "documents": documents.get("document_count", 0),
            "pdfs": documents.get("pdf_count", 0),
            "word_documents": documents.get("word_count", 0),
            "spreadsheets": documents.get("spreadsheet_count", 0),
            "presentations": documents.get("presentation_count", 0),
            "internal_documents": documents.get("internal_documents", 0),
            "external_documents": documents.get("external_documents", 0),
            "unique_domains": documents.get("unique_domains", []),
        }

    if "media" in reports:
        media = reports.get("media") or {}
        summary["media_totals"] = {
            "total_media": media.get("total_media", 0),
            "unique_media": media.get("unique_media", 0),
            "pages_with_media": media.get("pages_with_media", 0),
            "images": media.get("image_count", 0),
            "videos": media.get("video_count", 0),
            "iframes": media.get("iframe_count", 0),
            "background_images": media.get("background_image_count", 0),
            "external_media": media.get("external_media_count", 0),
            "hero_candidates": media.get("hero_candidates", 0),
            "logo_candidates": media.get("logo_candidates", 0),
            "product_candidates": media.get("product_candidates", 0),
            "unique_domains": media.get("unique_domains", []),
        }

    if "forms" in reports:
        forms = reports.get("forms") or {}
        summary["form_totals"] = {
            "forms": forms.get("form_count", 0),
            "controls": forms.get("total_controls", 0),
            "required_controls": forms.get("required_controls", 0),
            "unlabeled_controls": forms.get("unlabeled_controls", 0),
            "sensitive_controls": forms.get("sensitive_controls", 0),
            "search_forms": forms.get("search_forms", 0),
            "auth_forms": forms.get("auth_forms", 0),
            "newsletter_forms": forms.get("newsletter_forms", 0),
            "contact_forms": forms.get("contact_forms", 0),
            "external_action_forms": forms.get("external_action_forms", 0),
        }

    if "seo" in reports:
        seo = reports.get("seo") or {}
        summary["seo_totals"] = _build_seo_totals(seo)

    return summary


def _build_seo_totals(seo: dict[str, Any]) -> dict[str, Any]:
    page_reports = seo.get("page_reports") or []

    if page_reports:
        return {
            "page_count": len(page_reports),
            "pages_with_title": sum(1 for item in page_reports if item.get("has_title")),
            "pages_with_description": sum(1 for item in page_reports if item.get("has_description")),
            "pages_with_canonical": sum(1 for item in page_reports if item.get("has_canonical")),
            "pages_with_open_graph": sum(1 for item in page_reports if item.get("has_open_graph")),
            "pages_with_twitter": sum(1 for item in page_reports if item.get("has_twitter")),
            "pages_with_json_ld": sum(1 for item in page_reports if item.get("has_json_ld")),
            "average_score": _average(
                float(item.get("score") or 0)
                for item in page_reports
            ),
            "grades": _count_values(
                str(item.get("grade") or "unknown")
                for item in page_reports
            ),
        }

    return {
        "page_count": 1 if seo else 0,
        "pages_with_title": 1 if seo.get("has_title") else 0,
        "pages_with_description": 1 if seo.get("has_description") else 0,
        "pages_with_canonical": 1 if seo.get("has_canonical") else 0,
        "pages_with_open_graph": 1 if seo.get("has_open_graph") else 0,
        "pages_with_twitter": 1 if seo.get("has_twitter") else 0,
        "pages_with_json_ld": 1 if seo.get("has_json_ld") else 0,
        "average_score": float(seo.get("score") or 0),
        "grades": {str(seo.get("grade") or "unknown"): 1} if seo else {},
    }


def _average(values: Any) -> float:
    values = list(values)
    if not values:
        return 0.0

    return round(sum(values) / len(values), 4)


def _count_values(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}

    for value in values:
        counts[value] = counts.get(value, 0) + 1

    return counts