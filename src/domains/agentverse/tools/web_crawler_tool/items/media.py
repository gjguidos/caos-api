from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urljoin, urlparse

from src.domains.agentverse.tools.web_crawler_tool.constants import (
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
)
from src.domains.agentverse.tools.web_crawler_tool.types import (
    MediaInventoryItem,
)

_MEDIA_TYPES = {"image", "video", "iframe", "background"}


def media_item_from_asset_observation(
    *,
    observation: Mapping[str, Any],
    page_url: str | None = None,
) -> MediaInventoryItem | None:
    """
    Convert one persisted asset observation into the canonical
    MediaInventoryItem used by media reports.

    Expected observation fields:

        asset_type
        url
        source_page
        alt
        title
        width
        height
        loading
        metadata
    """

    asset_type = str(
        observation.get("asset_type")
        or ""
    ).strip().lower()

    subtype = str(
        observation.get("subtype")
        or observation.get("kind")
        or observation.get("type")
        or ""
    ).strip().lower()

    if asset_type == "media":
        media_type = subtype
    else:
        media_type = asset_type

    if media_type not in _MEDIA_TYPES:
        return None

    raw_url = str(
        observation.get("url")
        or observation.get("src")
        or observation.get("asset_url")
        or ""
    ).strip()

    source_page = str(
        observation.get("source_page")
        or observation.get("page_url")
        or page_url
        or ""
    ).strip()

    if not raw_url or not source_page:
        return None

    src = urljoin(source_page, raw_url)
    parsed_src = urlparse(src)
    parsed_page = urlparse(source_page)

    domain = parsed_src.netloc.lower() or None
    page_domain = parsed_page.netloc.lower()

    path = parsed_src.path or ""
    path_obj = PurePosixPath(path)

    extension = path_obj.suffix.lower() or None
    filename = path_obj.name or None
    stem = path_obj.stem or None

    is_image = (
        asset_type in {"image", "background"}
        or extension in IMAGE_EXTENSIONS
    )
    is_video = (
        asset_type == "video"
        or extension in VIDEO_EXTENSIONS
    )
    is_iframe = asset_type == "iframe"
    is_background = asset_type == "background"

    metadata = observation.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    alt = _optional_string(observation.get("alt"))
    title = _optional_string(observation.get("title"))
    loading = _optional_string(observation.get("loading"))

    is_lazy_loaded = (
        loading == "lazy"
        or "lazy" in _metadata_text(metadata)
    )

    is_external = bool(
        domain
        and page_domain
        and domain != page_domain
    )

    return MediaInventoryItem(
        src=src,
        source_page=source_page,
        kind=asset_type,
        alt=alt,
        title=title,
        width=observation.get("width"),
        height=observation.get("height"),
        loading=loading,
        role=_role_for_asset_type(asset_type),
        domain=domain,
        path=path,
        filename=filename,
        stem=stem,
        extension=extension,
        is_image=is_image,
        is_video=is_video,
        is_iframe=is_iframe,
        is_background=is_background,
        is_logo=False,
        is_icon=False,
        is_svg=extension == ".svg",
        is_lazy_loaded=is_lazy_loaded,
        is_external=is_external,
        is_download_candidate=is_image or is_video,
        occurrences=1,
        found_in_pages=[source_page],
        metadata=metadata,
    )


def media_items_from_asset_observations(
    *,
    observations: Iterable[Mapping[str, Any]],
) -> list[MediaInventoryItem]:
    """
    Convert and merge observations by normalized media URL.

    The returned list contains one item per unique media URL.
    Occurrences and found_in_pages retain the site-level evidence.
    """

    by_src: dict[str, MediaInventoryItem] = {}

    for observation in observations:
        item = media_item_from_asset_observation(
            observation=observation,
        )

        if item is None:
            continue

        existing = by_src.get(item.src)

        if existing is None:
            by_src[item.src] = item
            continue

        existing.occurrences += item.occurrences

        existing.found_in_pages = sorted(
            set(existing.found_in_pages or [])
            | set(item.found_in_pages or [])
        )

        if not existing.alt and item.alt:
            existing.alt = item.alt

        if not existing.title and item.title:
            existing.title = item.title

        if not existing.metadata and item.metadata:
            existing.metadata = item.metadata

    return list(by_src.values())


def _role_for_asset_type(asset_type: str) -> str:
    if asset_type == "background":
        return "background"

    if asset_type == "video":
        return "video"

    if asset_type == "iframe":
        return "embed"

    return "content"


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None

    value = str(value).strip()
    return value or None


def _metadata_text(metadata: Mapping[str, Any]) -> str:
    return " ".join(
        str(value).lower()
        for value in metadata.values()
    )