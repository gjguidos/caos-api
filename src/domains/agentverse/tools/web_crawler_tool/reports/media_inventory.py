from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from src.domains.agentverse.tools.web_crawler_tool.constants import (
    DECORATIVE_HINTS,
    HERO_HINTS,
    ICON_HINTS,
    IMAGE_EXTENSIONS,
    LOGO_HINTS,
    PRODUCT_HINTS,
    VIDEO_EXTENSIONS,
)
from src.domains.agentverse.tools.web_crawler_tool.items.media import (
    media_items_from_asset_observations,
)
from src.domains.agentverse.tools.web_crawler_tool.types import (
    MediaInventoryItem,
    PageMediaInventoryReport,
    SiteMediaInventoryReport,
)


def build_page_media_inventory_report(
    *,
    html: str,
    page_url: str,
    html_report: dict[str, Any] | None = None,
    soup: BeautifulSoup | None = None,
) -> PageMediaInventoryReport:
    html_report = html_report or {}

    items: list[MediaInventoryItem] = []

    items.extend(
        _extract_from_html_report(
            html_report=html_report,
            page_url=page_url,
        )
    )

    items.extend(
        _extract_background_images_from_html(
            html=html,
            page_url=page_url,
            soup=soup,
        )
    )

    items = _dedupe_page_items(items)

    domains = sorted(
        {
            item.domain
            for item in items
            if item.domain
        }
    )

    return PageMediaInventoryReport(
        total_media=len(items),
        image_count=sum(1 for item in items if item.is_image),
        video_count=sum(1 for item in items if item.is_video),
        iframe_count=sum(1 for item in items if item.is_iframe),
        background_image_count=sum(1 for item in items if item.is_background),
        svg_count=sum(1 for item in items if item.is_svg),
        external_media_count=sum(1 for item in items if item.is_external),
        lazy_loaded_count=sum(1 for item in items if item.is_lazy_loaded),
        logo_candidates=sum(1 for item in items if item.is_logo),
        icon_candidates=sum(1 for item in items if item.is_icon),
        hero_candidates=sum(1 for item in items if item.role == "hero_candidate"),
        product_candidates=sum(1 for item in items if item.role == "product_candidate"),
        unique_domains=domains,
        items=items,
    )


def build_site_media_inventory_report(
    *,
    pages: list[dict[str, Any]],
    asset_observations: list[dict[str, Any]] | None = None,
) -> SiteMediaInventoryReport:
    """
    Build the site-level media report.

    When asset_observations are provided, they are the canonical source.
    The page-report fallback exists only for callers that have not yet
    been migrated to the persisted asset observations.
    """

    if asset_observations is not None:
        items = media_items_from_asset_observations(
            observations=asset_observations,
        )

        total_media = sum(
            item.occurrences or 0
            for item in items
        )

        pages_with_media = len(
            {
                page_url
                for item in items
                for page_url in item.found_in_pages or []
                if page_url
            }
        )

    else:
        items = _collect_site_items_from_pages(pages)

        total_media = sum(
            int(
                (page.get("reports") or {})
                .get("media", {})
                .get("total_media")
                or 0
            )
            for page in pages
        )

        pages_with_media = sum(
            1
            for page in pages
            if int(
                (page.get("reports") or {})
                .get("media", {})
                .get("total_media")
                or 0
            ) > 0
        )

    domains = sorted(
        {
            item.domain
            for item in items
            if item.domain
        }
    )

    return SiteMediaInventoryReport(
        total_media=total_media,
        unique_media=len(items),
        pages_with_media=pages_with_media,
        image_count=sum(
            1 for item in items if item.is_image
        ),
        video_count=sum(
            1 for item in items if item.is_video
        ),
        iframe_count=sum(
            1 for item in items if item.is_iframe
        ),
        background_image_count=sum(
            1 for item in items if item.is_background
        ),
        svg_count=sum(
            1 for item in items if item.is_svg
        ),
        external_media_count=sum(
            1 for item in items if item.is_external
        ),
        lazy_loaded_count=sum(
            1 for item in items if item.is_lazy_loaded
        ),
        logo_candidates=sum(
            1 for item in items if item.is_logo
        ),
        icon_candidates=sum(
            1 for item in items if item.is_icon
        ),
        hero_candidates=sum(
            1
            for item in items
            if item.role == "hero_candidate"
        ),
        product_candidates=sum(
            1
            for item in items
            if item.role == "product_candidate"
        ),
        unique_domains=domains,
        items=items,
    )


def _extract_from_html_report(
    *,
    html_report: dict[str, Any],
    page_url: str,
) -> list[MediaInventoryItem]:
    media = html_report.get("media") or {}
    items: list[MediaInventoryItem] = []

    for image in media.get("images") or []:
        if not isinstance(image, dict):
            continue

        src = image.get("src")
        if not src:
            continue

        items.append(
            classify_media_item(
                raw_src=str(src),
                page_url=page_url,
                kind="image",
                alt=image.get("alt"),
                title=image.get("title"),
                width=image.get("width"),
                height=image.get("height"),
                loading=image.get("loading"),
                metadata={
                    "classes": image.get("classes") or [],
                },
            )
        )

    for video in media.get("videos") or []:
        if not isinstance(video, dict):
            continue

        src = video.get("src")
        if not src:
            continue

        items.append(
            classify_media_item(
                raw_src=str(src),
                page_url=page_url,
                kind="video",
                title=video.get("title"),
                metadata={
                    "controls": video.get("controls"),
                    "autoplay": video.get("autoplay"),
                },
            )
        )

    for iframe in media.get("iframes") or []:
        if not isinstance(iframe, dict):
            continue

        src = iframe.get("src")
        if not src:
            continue

        items.append(
            classify_media_item(
                raw_src=str(src),
                page_url=page_url,
                kind="iframe",
                title=iframe.get("title"),
                width=iframe.get("width"),
                height=iframe.get("height"),
            )
        )

    return items


def _extract_background_images_from_html(
    *,
    html: str,
    page_url: str,
    soup: BeautifulSoup | None = None,
) -> list[MediaInventoryItem]:
    if soup is None:
        soup = BeautifulSoup(html or "", "html.parser")
    items: list[MediaInventoryItem] = []

    for node in soup.find_all(style=True):
        if not isinstance(node, Tag):
            continue

        style = str(node.get("style") or "")
        urls = _extract_css_urls(style)

        for raw_src in urls:
            items.append(
                classify_media_item(
                    raw_src=raw_src,
                    page_url=page_url,
                    kind="background",
                    metadata={
                        "tag": node.name,
                        "id": node.get("id"),
                        "classes": node.get("class") or [],
                    },
                )
            )

    return items


def classify_media_item(
    *,
    raw_src: str,
    page_url: str,
    kind: str,
    alt: str | None = None,
    title: str | None = None,
    width: str | int | None = None,
    height: str | int | None = None,
    loading: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> MediaInventoryItem:
    src = urljoin(page_url, raw_src.strip())
    parsed = urlparse(src)
    page_domain = urlparse(page_url).netloc.lower()
    domain = parsed.netloc.lower() or None
    path = parsed.path or ""
    path_obj = PurePosixPath(path)
    filename = path_obj.name or None
    stem = path_obj.stem or None
    extension = path_obj.suffix.lower() or None

    is_image = kind in {"image", "background"} or extension in IMAGE_EXTENSIONS
    is_video = kind == "video" or extension in VIDEO_EXTENSIONS
    is_iframe = kind == "iframe"
    is_background = kind == "background"
    is_svg = extension == ".svg" or src.lower().endswith(".svg")
    is_lazy_loaded = loading == "lazy" or "lazy" in _metadata_text(metadata)
    is_external = bool(domain and domain != page_domain)
    is_download_candidate = is_image or is_video

    blob = " ".join(
        [
            src.lower(),
            str(alt or "").lower(),
            str(title or "").lower(),
            _metadata_text(metadata),
        ]
    )

    is_logo = any(hint in blob for hint in LOGO_HINTS)
    is_icon = any(hint in blob for hint in ICON_HINTS) or _looks_like_icon(width, height, path)
    role = _infer_media_role(
        blob=blob,
        kind=kind,
        is_logo=is_logo,
        is_icon=is_icon,
        width=width,
        height=height,
    )

    return MediaInventoryItem(
        src=src,
        source_page=page_url,
        kind=kind,
        alt=alt,
        title=title,
        width=width,
        height=height,
        loading=loading,
        role=role,
        domain=domain,
        path=path,
        filename=filename,
        stem=stem,
        extension=extension,
        is_image=is_image,
        is_video=is_video,
        is_iframe=is_iframe,
        is_background=is_background,
        is_logo=is_logo,
        is_icon=is_icon,
        is_svg=is_svg,
        is_lazy_loaded=is_lazy_loaded,
        is_external=is_external,
        is_download_candidate=is_download_candidate,
        occurrences=1,
        found_in_pages=[page_url],
        metadata=metadata or {},
    )


def _infer_media_role(
    *,
    blob: str,
    kind: str,
    is_logo: bool,
    is_icon: bool,
    width: str | int | None,
    height: str | int | None,
) -> str:
    if is_logo:
        return "logo"

    if is_icon:
        return "icon"

    if any(hint in blob for hint in HERO_HINTS):
        return "hero_candidate"

    if any(hint in blob for hint in PRODUCT_HINTS):
        return "product_candidate"

    if any(hint in blob for hint in DECORATIVE_HINTS):
        return "decorative"

    width_int = _to_int(width)
    height_int = _to_int(height)

    if width_int and height_int:
        if width_int >= 900 and height_int >= 300:
            return "hero_candidate"

        if width_int <= 96 and height_int <= 96:
            return "icon"

    if kind == "background":
        return "background"

    if kind == "video":
        return "video"

    if kind == "iframe":
        return "embed"

    return "content"


def _extract_css_urls(style: str) -> list[str]:
    urls: list[str] = []

    for match in re.finditer(r"url\((.*?)\)", style or "", re.IGNORECASE):
        value = match.group(1).strip().strip("'\"")

        if value and not value.startswith("data:"):
            urls.append(value)

    return urls


def _dedupe_page_items(items: list[MediaInventoryItem]) -> list[MediaInventoryItem]:
    by_src: dict[str, MediaInventoryItem] = {}

    for item in items:
        existing = by_src.get(item.src)

        if existing is None:
            by_src[item.src] = item
            continue

        existing.occurrences += 1

        pages = set(existing.found_in_pages or [])
        pages.update(item.found_in_pages or [])
        existing.found_in_pages = sorted(pages)

        if not existing.alt and item.alt:
            existing.alt = item.alt
        if not existing.title and item.title:
            existing.title = item.title

    return list(by_src.values())


def _looks_like_icon(
    width: str | int | None,
    height: str | int | None,
    path: str,
) -> bool:
    width_int = _to_int(width)
    height_int = _to_int(height)

    if width_int and height_int and width_int <= 96 and height_int <= 96:
        return True

    path_lower = path.lower()
    return "icon" in path_lower or "favicon" in path_lower


def _to_int(value: str | int | None) -> int | None:
    if value is None:
        return None

    if isinstance(value, int):
        return value

    try:
        return int(str(value).replace("px", "").strip())
    except ValueError:
        return None


def _metadata_text(metadata: dict[str, Any] | None) -> str:
    if not metadata:
        return ""

    return " ".join(str(value).lower() for value in metadata.values())

def _collect_site_items_from_pages(
    pages: list[dict[str, Any]],
) -> list[MediaInventoryItem]:
    by_src: dict[str, MediaInventoryItem] = {}

    for page in pages:
        page_url = page.get("final_url") or page.get("url") or ""
        reports = page.get("reports") or {}
        media_report = reports.get("media") or {}

        for raw_item in media_report.get("items") or []:
            if not isinstance(raw_item, dict):
                continue

            item = MediaInventoryItem(**raw_item)
            item.source_page = item.source_page or page_url

            existing = by_src.get(item.src)

            if existing is None:
                item.found_in_pages = sorted(
                    set(item.found_in_pages or [item.source_page])
                )
                by_src[item.src] = item
                continue

            existing.occurrences += item.occurrences or 1
            existing.found_in_pages = sorted(
                set(existing.found_in_pages or [])
                | set(item.found_in_pages or [])
                | (
                    {item.source_page}
                    if item.source_page
                    else set()
                )
            )

            if not existing.alt and item.alt:
                existing.alt = item.alt

            if not existing.title and item.title:
                existing.title = item.title

            if (
                existing.role == "unknown"
                and item.role != "unknown"
            ):
                existing.role = item.role

    return list(by_src.values())

