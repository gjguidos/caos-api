from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse

from src.domains.agentverse.tools.web_crawler_tool.constants import (
    DOCUMENT_EXTENSIONS,
    DOCUMENT_MIME_HINTS,
)
from src.domains.agentverse.tools.web_crawler_tool.items.documents import (
    document_items_from_asset_observations,
)
from src.domains.agentverse.tools.web_crawler_tool.types import (
    DocumentInventoryItem,
    DocumentInventoryReport,
)


def build_document_inventory_report(
    *,
    page_url: str,
    links_report: dict[str, Any] | None = None,
    content_blocks_report: dict[str, Any] | None = None,
) -> DocumentInventoryReport:
    links_report = links_report or {}
    content_blocks_report = content_blocks_report or {}

    items: list[DocumentInventoryItem] = []

    for raw_link in links_report.get("items") or []:
        if not isinstance(raw_link, dict):
            continue

        item = classify_document_link(
            raw_link=raw_link,
            page_url=page_url,
            context_text=find_link_context(
                url=raw_link.get("url") or "",
                content_blocks_report=content_blocks_report,
            ),
        )

        if item:
            items.append(item)

    items = dedupe_documents(items)

    domains = sorted(
        {
            item.domain
            for item in items
            if item.domain
        }
    )

    return DocumentInventoryReport(
        document_count=len(items),
        pdf_count=sum(1 for item in items if item.document_type == "pdf"),
        word_count=sum(1 for item in items if item.document_type == "word"),
        spreadsheet_count=sum(1 for item in items if item.document_type == "spreadsheet"),
        presentation_count=sum(1 for item in items if item.document_type == "presentation"),
        text_count=sum(1 for item in items if item.document_type == "text"),
        data_count=sum(1 for item in items if item.document_type == "data"),
        internal_documents=sum(1 for item in items if item.is_internal),
        external_documents=sum(1 for item in items if item.is_external),
        unique_domains=domains,
        items=items,
    )


def classify_document_link(
    *,
    raw_link: dict[str, Any],
    page_url: str,
    context_text: str = "",
) -> DocumentInventoryItem | None:
    url = str(raw_link.get("url") or "").strip()
    if not url:
        return None

    parsed = urlparse(url)
    page_domain = urlparse(page_url).netloc.lower()

    path = parsed.path or ""
    path_obj = PurePosixPath(path)

    filename = path_obj.name or None
    stem = path_obj.stem or None
    extension = path_obj.suffix.lower() or None

    domain = parsed.netloc.lower() or None

    document_type = infer_document_type(
        url=url,
        path=path,
        extension=extension,
        mime_hint=raw_link.get("mime_hint"),
        is_download=bool(raw_link.get("is_download")),
    )

    if document_type == "unknown":
        return None

    scope = raw_link.get("scope") or "unknown"

    return DocumentInventoryItem(
        url=url,
        source_page=raw_link.get("source_url") or page_url,
        anchor_text=raw_link.get("text") or "",
        context_text=context_text or raw_link.get("text") or "",
        document_type=document_type,
        extension=extension,
        filename=filename,
        stem=stem,
        mime_hint=raw_link.get("mime_hint"),
        is_pdf=document_type == "pdf",
        is_download=bool(raw_link.get("is_download")),
        is_internal=scope == "internal" or bool(domain and domain == page_domain),
        is_external=scope == "external" or bool(domain and domain != page_domain),
        domain=domain,
        path=path,
        evidence_role=infer_document_evidence_role(
            anchor_text=raw_link.get("text") or "",
            url=url,
            document_type=document_type,
            context_text=context_text,
        ),
        source_field=raw_link.get("source_field") or "links",
        metadata={
            "link_type": raw_link.get("link_type"),
            "scope": scope,
            "source_metadata": raw_link.get("metadata") or {},
        },
    )


def infer_document_type(
    *,
    url: str,
    path: str,
    extension: str | None,
    mime_hint: str | None = None,
    is_download: bool = False,
) -> str:
    if mime_hint:
        normalized_mime = mime_hint.split(";", 1)[0].strip().lower()
        if normalized_mime in DOCUMENT_MIME_HINTS:
            return DOCUMENT_MIME_HINTS[normalized_mime]

    if extension and extension in DOCUMENT_EXTENSIONS:
        return DOCUMENT_EXTENSIONS[extension]

    lower_url = url.lower()

    # Handles signed/document URLs where extension may be hidden in query params.
    for ext, doc_type in DOCUMENT_EXTENSIONS.items():
        if ext in lower_url:
            return doc_type

    # Do not classify every download as a document without stronger evidence.
    return "unknown"


def infer_document_evidence_role(
    *,
    anchor_text: str,
    url: str,
    document_type: str,
    context_text: str,
) -> str:
    blob = f"{anchor_text} {url} {context_text}".lower()

    if document_type == "pdf" and any(
        word in blob
        for word in [
            "sustainability",
            "annual report",
            "strategy",
            "impact report",
            "climate",
            "environment",
            "esg",
        ]
    ):
        return "primary_supporting_document_candidate"

    if any(word in blob for word in ["policy", "statement", "code of conduct"]):
        return "policy_document_candidate"

    if any(word in blob for word in ["certificate", "certification", "audit"]):
        return "certification_document_candidate"

    if any(word in blob for word in ["report", "results", "disclosure"]):
        return "supporting_report_candidate"

    return "document_candidate"


def find_link_context(
    *,
    url: str,
    content_blocks_report: dict[str, Any],
    max_chars: int = 700,
) -> str:
    if not url:
        return ""

    for block in content_blocks_report.get("blocks") or []:
        if not isinstance(block, dict):
            continue

        for link in block.get("links") or []:
            if not isinstance(link, dict):
                continue

            if link.get("href") == url:
                heading = block.get("heading") or ""
                text = block.get("text") or block.get("text_preview") or ""
                context = f"{heading}\n{text}".strip()
                return context[:max_chars]

    return ""


def dedupe_documents(items: list[DocumentInventoryItem]) -> list[DocumentInventoryItem]:
    by_url: dict[str, DocumentInventoryItem] = {}

    for item in items:
        existing = by_url.get(item.url)

        if existing is None:
            by_url[item.url] = item
            continue

        if not existing.anchor_text and item.anchor_text:
            existing.anchor_text = item.anchor_text

        if not existing.context_text and item.context_text:
            existing.context_text = item.context_text

        if existing.evidence_role == "document_candidate" and item.evidence_role != "document_candidate":
            existing.evidence_role = item.evidence_role

        existing.metadata.update(item.metadata)

    return list(by_url.values())

def build_site_document_inventory_report(
    *,
    asset_observations: list[dict[str, Any]],
) -> DocumentInventoryReport:

    items = document_items_from_asset_observations(
        observations=asset_observations,
    )

    domains = sorted(
        {
            item.domain
            for item in items
            if item.domain
        }
    )

    return DocumentInventoryReport(
        document_count=len(items),
        pdf_count=sum(
            1 for item in items
            if item.document_type == "pdf"
        ),
        word_count=sum(
            1 for item in items
            if item.document_type == "word"
        ),
        spreadsheet_count=sum(
            1 for item in items
            if item.document_type == "spreadsheet"
        ),
        presentation_count=sum(
            1 for item in items
            if item.document_type == "presentation"
        ),
        text_count=sum(
            1 for item in items
            if item.document_type == "text"
        ),
        data_count=sum(
            1 for item in items
            if item.document_type == "data"
        ),
        internal_documents=sum(
            1 for item in items
            if item.is_internal
        ),
        external_documents=sum(
            1 for item in items
            if item.is_external
        ),
        unique_domains=domains,
        items=items,
    )