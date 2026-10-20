from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urljoin, urlparse

from src.domains.agentverse.tools.web_crawler_tool.constants import (
    DOCUMENT_EXTENSIONS,
    DOCUMENT_MIME_HINTS,
)
from src.domains.agentverse.tools.web_crawler_tool.types import (
    DocumentInventoryItem,
)

_DOCUMENT_TYPES = {
    "pdf",
    "word",
    "spreadsheet",
    "presentation",
    "text",
    "data",
}


def document_item_from_asset_observation(
    *,
    observation: Mapping[str, Any],
) -> DocumentInventoryItem | None:
    """
    Convert one persisted document asset observation into a
    DocumentInventoryItem.
    """

    asset_type = str(
        observation.get("asset_type") or ""
    ).strip().lower()

    subtype = str(
        observation.get("subtype") or ""
    ).strip().lower()

    if asset_type == "document":
        document_type = subtype or None
    elif asset_type in _DOCUMENT_TYPES:
        # Supports the current flattened observation shape:
        # asset_type="pdf"
        document_type = asset_type
    else:
        return None

    raw_url = str(
        observation.get("url")
        or observation.get("asset_url")
        or ""
    ).strip()

    source_page = str(
        observation.get("source_page")
        or observation.get("source_page_url")
        or ""
    ).strip()

    if not raw_url or not source_page:
        return None

    url = urljoin(source_page, raw_url)

    parsed_url = urlparse(url)
    parsed_page = urlparse(source_page)

    path = parsed_url.path or ""
    path_obj = PurePosixPath(path)

    extension = path_obj.suffix.lower() or None
    filename = path_obj.name or None
    stem = path_obj.stem or None
    domain = parsed_url.netloc.lower() or None

    metadata = observation.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    mime_hint = _extract_mime_hint(
        observation=observation,
        metadata=metadata,
    )

    if document_type not in _DOCUMENT_TYPES:
        document_type = _resolve_document_type(
            subtype=subtype,
            extension=extension,
            url=url,
            mime_hint=mime_hint,
        )

    if document_type is None:
        return None
    
    is_internal = _resolve_is_internal(
        observation=observation,
        domain=domain,
        page_domain=parsed_page.netloc.lower(),
    )

    return DocumentInventoryItem(
        url=url,
        source_page=source_page,
        anchor_text="",
        context_text="",
        document_type=document_type,
        extension=extension,
        filename=filename,
        stem=stem,
        mime_hint=mime_hint,
        is_pdf=document_type == "pdf",
        is_download=True,
        is_internal=is_internal,
        is_external=not is_internal,
        domain=domain,
        path=path,
        evidence_role="document_candidate",
        source_field="asset_state.observation",
        metadata={
            "asset_key": observation.get("asset_key"),
            "observation_key": observation.get("observation_key"),
            "asset_type": asset_type,
            "subtype": observation.get("subtype"),
            **metadata,
        },
    )


def document_items_from_asset_observations(
    *,
    observations: Iterable[Mapping[str, Any]],
) -> list[DocumentInventoryItem]:
    """
    Convert document observations and deduplicate them by URL.
    """

    by_url: dict[str, DocumentInventoryItem] = {}

    for observation in observations:
        item = document_item_from_asset_observation(
            observation=observation,
        )

        if item is None:
            continue

        existing = by_url.get(item.url)

        if existing is None:
            by_url[item.url] = item
            continue

        if not existing.source_page and item.source_page:
            existing.source_page = item.source_page

        if not existing.domain and item.domain:
            existing.domain = item.domain

        if not existing.extension and item.extension:
            existing.extension = item.extension

        if not existing.filename and item.filename:
            existing.filename = item.filename

        if not existing.mime_hint and item.mime_hint:
            existing.mime_hint = item.mime_hint

        existing.metadata.update(item.metadata)

    return list(by_url.values())


def _resolve_document_type(
    *,
    subtype: Any,
    extension: str | None,
    url: str,
    mime_hint: str | None,
) -> str | None:
    normalized_subtype = str(subtype or "").strip().lower()

    if normalized_subtype in _DOCUMENT_TYPES:
        return normalized_subtype

    if mime_hint:
        normalized_mime = mime_hint.split(";", 1)[0].strip().lower()
        resolved = DOCUMENT_MIME_HINTS.get(normalized_mime)

        if resolved:
            return resolved

    if extension:
        resolved = DOCUMENT_EXTENSIONS.get(extension)

        if resolved:
            return resolved

    lower_url = url.lower()

    for document_extension, document_type in DOCUMENT_EXTENSIONS.items():
        if document_extension in lower_url:
            return document_type

    return None


def _extract_mime_hint(
    *,
    observation: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> str | None:
    value = (
        observation.get("mime_hint")
        or observation.get("content_type")
        or metadata.get("mime_hint")
        or metadata.get("content_type")
    )

    if value is None:
        return None

    value = str(value).strip()
    return value or None


def _resolve_is_internal(
    *,
    observation: Mapping[str, Any],
    domain: str | None,
    page_domain: str,
) -> bool:
    explicit_value = observation.get("is_internal")

    if explicit_value is not None:
        return bool(explicit_value)

    return bool(
        domain
        and page_domain
        and domain == page_domain
    )