from __future__ import annotations

from collections import Counter
from typing import Any
from urllib.parse import urljoin, urlparse

from src.domains.agentverse.tools.web_crawler_tool.types import (
    LinkInventoryItem,
    LinkInventoryReport,
)

DOWNLOAD_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".zip",
    ".rar",
    ".7z",
    ".csv",
    ".json",
    ".xml",
    ".txt",
}

SOCIAL_DOMAINS = {
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "youtu.be",
    "tiktok.com",
    "pinterest.com",
    "threads.net",
}

CTA_KEYWORDS = {
    "apply",
    "sign up",
    "join",
    "start",
    "get started",
    "learn more",
    "read more",
    "contact",
    "book",
    "buy",
    "shop",
    "explore",
    "download",
    "subscribe",
    "register",
    "login",
    "log in",
}


def build_link_inventory_report(
    *,
    page_url: str,
    links: list[str],
    html_report: dict[str, Any] | None = None,
) -> LinkInventoryReport:
    html_report = html_report or {}

    extracted_links = _extract_links_with_text_from_html_report(html_report)

    items_by_url: dict[str, LinkInventoryItem] = {}

    for raw_link in links or []:
        item = classify_link(
            raw_url=raw_link,
            text="",
            page_url=page_url,
            source_field="links",
        )
        items_by_url.setdefault(item.url, item)

    for link_data in extracted_links:
        raw_url = str(link_data.get("href") or "").strip()
        text = str(link_data.get("text") or "").strip()

        if not raw_url:
            continue

        item = classify_link(
            raw_url=raw_url,
            text=text,
            page_url=page_url,
            source_field="html_report.interactive.links",
            metadata={
                "aria_label": link_data.get("aria_label"),
                "target": link_data.get("target"),
                "rel": link_data.get("rel"),
            },
        )

        existing = items_by_url.get(item.url)
        if existing:
            if not existing.text and item.text:
                existing.text = item.text
            existing.metadata.update(
                {k: v for k, v in item.metadata.items() if v is not None}
            )
        else:
            items_by_url[item.url] = item

    items = list(items_by_url.values())

    domain_counts = Counter(
        item.domain for item in items if item.domain
    )

    return LinkInventoryReport(
        total_links=len(links or []) + len(extracted_links),
        unique_links=len(items),
        internal_links=sum(1 for item in items if item.scope == "internal"),
        external_links=sum(1 for item in items if item.scope == "external"),
        same_page_links=sum(1 for item in items if item.scope == "same_page"),
        special_links=sum(1 for item in items if item.scope == "special"),
        download_links=sum(1 for item in items if item.is_download),
        social_links=sum(1 for item in items if item.is_social),
        mailto_links=sum(1 for item in items if item.is_mailto),
        tel_links=sum(1 for item in items if item.is_tel),
        cta_candidates=sum(1 for item in items if item.is_cta_candidate),
        unique_domains=sorted(domain_counts.keys()),
        items=items,
    )


def classify_link(
    *,
    raw_url: str,
    text: str,
    page_url: str,
    source_field: str,
    metadata: dict[str, Any] | None = None,
) -> LinkInventoryItem:
    raw_url = raw_url.strip()
    absolute_url = urljoin(page_url, raw_url)

    parsed_page = urlparse(page_url)
    parsed = urlparse(absolute_url)

    scheme = parsed.scheme.lower()
    domain = parsed.netloc.lower() or None
    path = parsed.path or ""

    is_mailto = scheme == "mailto"
    is_tel = scheme == "tel"
    is_same_page = raw_url.startswith("#")

    if is_mailto or is_tel:
        scope = "special"
    elif is_same_page:
        scope = "same_page"
    elif domain and domain == parsed_page.netloc.lower():
        scope = "internal"
    elif domain:
        scope = "external"
    else:
        scope = "unknown"

    normalized_domain = _normalize_domain(domain)

    item_text = " ".join((text or "").split()).strip()
    text_lower = item_text.lower()
    url_lower = absolute_url.lower()

    is_download = any(path.lower().endswith(ext) for ext in DOWNLOAD_EXTENSIONS)
    is_social = bool(
        normalized_domain and any(
            normalized_domain == social or normalized_domain.endswith("." + social)
            for social in SOCIAL_DOMAINS
        )
    )

    is_cta_candidate = any(keyword in text_lower for keyword in CTA_KEYWORDS)

    link_type = _infer_link_type(
        scope=scope,
        is_download=is_download,
        is_social=is_social,
        is_mailto=is_mailto,
        is_tel=is_tel,
        text_lower=text_lower,
        url_lower=url_lower,
    )

    return LinkInventoryItem(
        url=absolute_url,
        text=item_text,
        source_url=page_url,
        link_type=link_type,
        scope=scope,
        domain=domain,
        path=path,
        scheme=scheme,
        is_download=is_download,
        is_social=is_social,
        is_mailto=is_mailto,
        is_tel=is_tel,
        is_cta_candidate=is_cta_candidate,
        source_field=source_field,
        metadata=metadata or {},
    )


def _extract_links_with_text_from_html_report(
    html_report: dict[str, Any],
) -> list[dict[str, Any]]:
    interactive = html_report.get("interactive") or {}
    links = interactive.get("links") or []

    if isinstance(links, list):
        return [item for item in links if isinstance(item, dict)]

    return []


def _normalize_domain(domain: str | None) -> str | None:
    if not domain:
        return None

    value = domain.lower().strip()

    if value.startswith("www."):
        value = value[4:]

    return value


def _infer_link_type(
    *,
    scope: str,
    is_download: bool,
    is_social: bool,
    is_mailto: bool,
    is_tel: bool,
    text_lower: str,
    url_lower: str,
) -> str:
    if is_mailto:
        return "email"
    if is_tel:
        return "phone"
    if is_download:
        return "download"
    if is_social:
        return "social"

    if "job" in text_lower or "career" in text_lower or "apply" in text_lower:
        return "career"

    if "privacy" in url_lower or "privacy" in text_lower:
        return "privacy"

    if "accessibility" in url_lower or "accessibility" in text_lower:
        return "accessibility"

    if "contact" in text_lower or "contact" in url_lower:
        return "contact"

    if scope == "internal":
        return "internal_navigation"

    if scope == "external":
        return "external_reference"

    if scope == "same_page":
        return "anchor"

    return "unknown"