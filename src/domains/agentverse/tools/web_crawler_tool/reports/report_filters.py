from __future__ import annotations

from typing import Any


def build_report_filters(
    *,
    pages: list[dict[str, Any]],
    media_extensions: list[str] | None = None,
    document_extensions: list[str] | None = None,
    media_filename: str | None = None,
    document_filename: str | None = None,
    media_role: str | None = None,
    document_type: str | None = None,
) -> dict[str, Any]:
    media_filters = build_media_filter_report(
        pages=pages,
        extensions=media_extensions,
        filename=media_filename,
        role=media_role,
    )

    document_filters = build_document_filter_report(
        pages=pages,
        extensions=document_extensions,
        filename=document_filename,
        document_type=document_type,
    )

    enabled_filters = {
        key: value
        for key, value in {
            "media": media_filters,
            "documents": document_filters,
        }.items()
        if value.get("enabled")
    }

    return {
        "contract_version": "crawler.report_filters.v1",
        "enabled": bool(enabled_filters),
        "filters": enabled_filters,
    }


def build_media_filter_report(
    *,
    pages: list[dict[str, Any]],
    extensions: list[str] | None = None,
    filename: str | None = None,
    role: str | None = None,
) -> dict[str, Any]:
    criteria = {
        "extensions": _normalize_extensions(extensions),
        "filename": _normalize_text(filename),
        "role": _normalize_text(role),
    }

    enabled = bool(
        criteria["extensions"]
        or criteria["filename"]
        or criteria["role"]
    )

    if not enabled:
        return {
            "enabled": False,
            "criteria": criteria,
            "match_count": 0,
            "items": [],
        }

    items: list[dict[str, Any]] = []

    for page in pages:
        page_url = page.get("final_url") or page.get("url") or ""
        page_title = page.get("title") or ""
        reports = page.get("reports") or {}
        media = reports.get("media") or {}

        for item in media.get("items") or []:
            if not isinstance(item, dict):
                continue

            if not _matches_media_item(
                item=item,
                criteria=criteria,
            ):
                continue

            items.append(
                {
                    "page_url": page_url,
                    "page_title": page_title,
                    **item,
                }
            )

    return {
        "enabled": True,
        "criteria": criteria,
        "match_count": len(items),
        "items": items,
    }


def build_document_filter_report(
    *,
    pages: list[dict[str, Any]],
    extensions: list[str] | None = None,
    filename: str | None = None,
    document_type: str | None = None,
) -> dict[str, Any]:
    criteria = {
        "extensions": _normalize_extensions(extensions),
        "filename": _normalize_text(filename),
        "document_type": _normalize_text(document_type),
    }

    enabled = bool(
        criteria["extensions"]
        or criteria["filename"]
        or criteria["document_type"]
    )

    if not enabled:
        return {
            "enabled": False,
            "criteria": criteria,
            "match_count": 0,
            "items": [],
        }

    items: list[dict[str, Any]] = []

    for page in pages:
        page_url = page.get("final_url") or page.get("url") or ""
        page_title = page.get("title") or ""
        reports = page.get("reports") or {}
        documents = reports.get("documents") or {}

        for item in documents.get("items") or []:
            if not isinstance(item, dict):
                continue

            if not _matches_document_item(
                item=item,
                criteria=criteria,
            ):
                continue

            items.append(
                {
                    "page_url": page_url,
                    "page_title": page_title,
                    **item,
                }
            )

    return {
        "enabled": True,
        "criteria": criteria,
        "match_count": len(items),
        "items": items,
    }


def _matches_media_item(
    *,
    item: dict[str, Any],
    criteria: dict[str, Any],
) -> bool:
    extensions = criteria.get("extensions") or []

    if extensions:
        item_extension = _normalize_extension(
            item.get("extension")
        )

        if item_extension not in extensions:
            return False

    filename = criteria.get("filename")

    if (
        filename
        and filename
        not in (
            _normalize_text(
                item.get("filename")
            )
            or ""
        )
    ):
        return False

    role = criteria.get("role")

    if (
        role
        and _normalize_text(
            item.get("role")
        )
        != role
    ):
        return False

    return True


def _matches_document_item(
    *,
    item: dict[str, Any],
    criteria: dict[str, Any],
) -> bool:
    extensions = criteria.get("extensions") or []

    if extensions:
        item_extension = _normalize_extension(
            item.get("extension")
        )

        if item_extension not in extensions:
            return False

    filename = criteria.get("filename")

    if (
        filename
        and filename
        not in (
            _normalize_text(
                item.get("filename")
            )
            or ""
        )
    ):
        return False

    document_type = criteria.get(
        "document_type"
    )

    if (
        document_type
        and _normalize_text(
            item.get("document_type")
        )
        != document_type
    ):
        return False

    return True


def _normalize_extensions(
    values: list[str] | None,
) -> list[str]:
    if not values:
        return []

    normalized: list[str] = []

    for value in values:
        extension = _normalize_extension(value)

        if (
            extension
            and extension not in normalized
        ):
            normalized.append(extension)

    return normalized


def _normalize_extension(
    value: Any,
) -> str | None:
    if value is None:
        return None

    text = str(value).strip().lower()

    if not text:
        return None

    if not text.startswith("."):
        text = f".{text}"

    return text


def _normalize_text(
    value: Any,
) -> str | None:
    if value is None:
        return None

    text = str(value).strip().lower()

    return text or None