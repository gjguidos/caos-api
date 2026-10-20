from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from src.domains.agentverse.tools.web_crawler_tool.helpers.resources import (
    filename_from_url_or_item as _filename_from_url_or_item,
)
from src.domains.agentverse.tools.web_crawler_tool.helpers.resources import (
    item_url as _item_url,
)


def apply_crawler_filters(
    *,
    raw_result: dict[str, Any],
    media_extensions: list[str] | None = None,
    media_filename: str | None = None,
    media_role: str | None = None,
    document_extensions: list[str] | None = None,
    document_filename: str | None = None,
    document_type: str | None = None,
) -> dict[str, Any]:
    """
    Applies media/document filters to crawled page reports.

    This does not remove pages. It filters inventory-like report entries
    and adds filtered result collections to raw_result.
    """

    pages = raw_result.get("pages") or []

    filtered_media: list[dict[str, Any]] = []
    filtered_documents: list[dict[str, Any]] = []

    normalized_media_extensions = _normalize_extensions(
        media_extensions
    )

    normalized_document_extensions = _normalize_extensions(
        document_extensions
    )

    for page in pages:
        page_url = page.get("final_url") or page.get("url") or ""
        page_title = page.get("title") or ""

        media_items = _extract_media_items(page)
        document_items = _extract_document_items(page)

        for item in media_items:
            normalized = _with_page_metadata(
                item=item,
                page_url=page_url,
                page_title=page_title,
            )

            if _matches_media_filter(
                item=normalized,
                extensions=normalized_media_extensions,
                filename=media_filename,
                role=media_role,
            ):
                filtered_media.append(normalized)

        for item in document_items:
            normalized = _with_page_metadata(
                item=item,
                page_url=page_url,
                page_title=page_title,
            )

            if _matches_document_filter(
                item=normalized,
                extensions=normalized_document_extensions,
                filename=document_filename,
                document_type=document_type,
            ):
                filtered_documents.append(normalized)

    filters = {
        "media_extensions": normalized_media_extensions,
        "media_filename": media_filename,
        "media_role": media_role,
        "document_extensions": normalized_document_extensions,
        "document_filename": document_filename,
        "document_type": document_type,
    }

    raw_result["filters"] = filters
    raw_result["filtered_media"] = filtered_media
    raw_result["filtered_documents"] = filtered_documents
    raw_result["filter_summary"] = {
        "media_count": len(filtered_media),
        "document_count": len(filtered_documents),
        "has_active_media_filter": bool(
            normalized_media_extensions
            or _has_text(media_filename)
            or _has_text(media_role)
        ),
        "has_active_document_filter": bool(
            normalized_document_extensions
            or _has_text(document_filename)
            or _has_text(document_type)
        ),
    }

    return raw_result


def _extract_media_items(
    page: dict[str, Any],
) -> list[dict[str, Any]]:
    reports = page.get("reports") or {}

    candidates = [
        reports.get("media"),
        page.get("media"),
        page.get("media_report"),
    ]

    for candidate in candidates:
        items = _coerce_items(candidate)

        if items:
            return items

    return []


def _extract_document_items(
    page: dict[str, Any],
) -> list[dict[str, Any]]:
    reports = page.get("reports") or {}

    candidates = [
        reports.get("documents"),
        page.get("documents"),
        page.get("document_report"),
    ]

    for candidate in candidates:
        items = _coerce_items(candidate)

        if items:
            return items

    return []


def _coerce_items(
    value: Any,
) -> list[dict[str, Any]]:
    if value is None:
        return []

    if isinstance(value, list):
        return [
            item
            for item in value
            if isinstance(item, dict)
        ]

    if isinstance(value, dict):
        for key in [
            "items",
            "media",
            "documents",
            "results",
        ]:
            nested = value.get(key)

            if isinstance(nested, list):
                return [
                    item
                    for item in nested
                    if isinstance(item, dict)
                ]

    return []


def _with_page_metadata(
    *,
    item: dict[str, Any],
    page_url: str,
    page_title: str,
) -> dict[str, Any]:
    return {
        **item,
        "page_url": page_url,
        "page_title": page_title,
    }


def _matches_media_filter(
    *,
    item: dict[str, Any],
    extensions: list[str] | None,
    filename: str | None,
    role: str | None,
) -> bool:
    url = _item_url(item)
    item_filename = _filename_from_url_or_item(
        item,
        url,
    )

    return all(
        [
            _matches_extensions(
                url=url,
                extensions=extensions,
            ),
            _matches_contains(
                value=item_filename,
                query=filename,
            ),
            _matches_equals_or_contains(
                value=str(
                    item.get("role")
                    or item.get("media_role")
                    or ""
                ),
                query=role,
            ),
        ]
    )


def _matches_document_filter(
    *,
    item: dict[str, Any],
    extensions: list[str] | None,
    filename: str | None,
    document_type: str | None,
) -> bool:
    url = _item_url(item)
    item_filename = _filename_from_url_or_item(
        item,
        url,
    )

    return all(
        [
            _matches_extensions(
                url=url,
                extensions=extensions,
            ),
            _matches_contains(
                value=item_filename,
                query=filename,
            ),
            _matches_equals_or_contains(
                value=str(
                    item.get("type")
                    or item.get("document_type")
                    or ""
                ),
                query=document_type,
            ),
        ]
    )


def _normalize_extensions(
    extensions: list[str] | None,
) -> list[str]:
    if not extensions:
        return []

    normalized: list[str] = []

    for value in extensions:
        text = str(value).strip().lower()

        if not text:
            continue

        if not text.startswith("."):
            text = f".{text}"

        if text not in normalized:
            normalized.append(text)

    return normalized


def _matches_extensions(
    *,
    url: str,
    extensions: list[str] | None,
) -> bool:
    if not extensions:
        return True

    path = urlparse(url).path.lower()

    return any(
        path.endswith(extension)
        for extension in extensions
    )


def _matches_contains(
    *,
    value: str,
    query: str | None,
) -> bool:
    if not query or not query.strip():
        return True

    return (
        query.strip().lower()
        in value.lower()
    )


def _matches_equals_or_contains(
    *,
    value: str,
    query: str | None,
) -> bool:
    if not query or not query.strip():
        return True

    normalized_value = value.strip().lower()
    normalized_query = query.strip().lower()

    return (
        normalized_value == normalized_query
        or normalized_query in normalized_value
    )


def _has_text(
    value: str | None,
) -> bool:
    return bool(
        value is not None
        and value.strip()
    )