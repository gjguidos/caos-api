from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from src.domains.agentverse.tools.web_crawler_tool.constants import (
    DOCUMENT_EXTENSIONS,
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
)


def domain_from_url(url: str) -> str:
    return (
        urlparse(url).hostname
        or ""
    ).lower()

def filename_from_url_or_item(
    item: dict[str, Any],
    url: str,
) -> str:
    explicit = (
        item.get("filename")
        or item.get("name")
        or item.get("title")
    )

    if explicit:
        return str(explicit)

    path = urlparse(url).path or ""
    return path.rsplit("/", 1)[-1]


def item_url(
    item: dict[str, Any],
) -> str:
    return str(
        item.get("url")
        or item.get("src")
        or item.get("href")
        or item.get("download_url")
        or ""
    )


def extension_from_url(
    url: str,
) -> str:
    path = urlparse(url).path or ""
    filename = path.rsplit("/", 1)[-1]

    if "." not in filename:
        return ""

    return "." + filename.rsplit(".", 1)[-1].lower()


def infer_filter_from_find(
    query: str | None,
) -> dict[str, Any]:
    if not query or not query.strip():
        return {}

    normalized = query.strip().lower()

    if (
        normalized in IMAGE_EXTENSIONS
        or normalized in VIDEO_EXTENSIONS
    ):
        return {
            "media_extensions": [normalized],
        }

    if normalized in DOCUMENT_EXTENSIONS:
        return {
            "document_extensions": [normalized],
        }

    return {}


def normalize_extension_list(
    value: Any,
) -> list[str]:
    if value is None:
        return []

    if isinstance(value, str):
        values = [
            item.strip()
            for item in value.split(",")
            if item.strip()
        ]
    elif isinstance(value, (list, tuple, set)):
        values = [
            str(item).strip()
            for item in value
            if str(item).strip()
        ]
    else:
        values = [
            str(value).strip()
        ]

    normalized: list[str] = []

    for extension in values:
        extension = extension.lower()

        if not extension.startswith("."):
            extension = f".{extension}"

        if extension not in normalized:
            normalized.append(extension)

    return normalized