from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from src.domains.agentverse.tools.web_crawler_tool.sitemap.models import (
    SitemapEntry,
    SitemapPartition,
    SitemapResource,
    SitemapTopology,
)

SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
IMAGE_NS = "http://www.google.com/schemas/sitemap-image/1.1"
VIDEO_NS = "http://www.google.com/schemas/sitemap-video/1.1"

SITEMAP_INDEX_TAG = (
    f"{{{SITEMAP_NS}}}sitemapindex"
)
URLSET_TAG = (
    f"{{{SITEMAP_NS}}}urlset"
)
SITEMAP_TAG = (
    f"{{{SITEMAP_NS}}}sitemap"
)
URL_TAG = (
    f"{{{SITEMAP_NS}}}url"
)
LOC_TAG = (
    f"{{{SITEMAP_NS}}}loc"
)


@dataclass(frozen=True)
class SitemapLocationDocument:
    """
    Lightweight representation used during recursive sitemap discovery.

    It deliberately contains only the URLs needed to continue traversal
    or build page acquisition targets.
    """

    source_type: Literal[
        "sitemap",
        "sitemap_index",
    ]

    sitemap_urls: list[str]
    page_urls: list[str]


def parse_sitemap_locations(
    *,
    xml: str,
) -> SitemapLocationDocument:
    """
    Lightweight streaming sitemap parser.

    Extracts only:
      sitemapindex -> sitemap/loc
      urlset       -> url/loc

    Uses end events only and exact sitemap.org tag comparisons on the
    common path. Completed sitemap/url records are immediately cleared.
    """

    sitemap_urls: list[str] = []
    page_urls: list[str] = []

    append_sitemap = sitemap_urls.append
    append_page = page_urls.append

    source_type: Literal[
        "sitemap",
        "sitemap_index",
    ] | None = None

    context = ET.iterparse(
        StringIO(xml),
        events=("end",),
    )

    for _, element in context:
        tag = element.tag

        # --------------------------------------------------------------
        # Standard sitemap index entry.
        # --------------------------------------------------------------
        if tag == SITEMAP_TAG:
            loc = element.find(
                LOC_TAG
            )

            if (
                loc is not None
                and loc.text
            ):
                value = loc.text.strip()

                if value:
                    append_sitemap(
                        value
                    )

            element.clear()
            continue

        # --------------------------------------------------------------
        # Standard urlset entry.
        # --------------------------------------------------------------
        if tag == URL_TAG:
            loc = element.find(
                LOC_TAG
            )

            if (
                loc is not None
                and loc.text
            ):
                value = loc.text.strip()

                if value:
                    append_page(
                        value
                    )

            element.clear()
            continue

        # --------------------------------------------------------------
        # Root elements arrive last because this is an end-event parser.
        # --------------------------------------------------------------
        if tag == SITEMAP_INDEX_TAG:
            source_type = "sitemap_index"
            continue

        if tag == URLSET_TAG:
            source_type = "sitemap"
            continue

    if source_type == "sitemap_index":
        return SitemapLocationDocument(
            source_type="sitemap_index",
            sitemap_urls=sitemap_urls,
            page_urls=[],
        )

    if source_type == "sitemap":
        return SitemapLocationDocument(
            source_type="sitemap",
            sitemap_urls=[],
            page_urls=page_urls,
        )

    # ------------------------------------------------------------------
    # Rare compatibility fallback for unusual/non-namespaced sitemap
    # documents.
    # ------------------------------------------------------------------
    return _parse_sitemap_locations_fallback(
        xml=xml,
    )

def _parse_sitemap_locations_fallback(
    *,
    xml: str,
) -> SitemapLocationDocument:
    root = ET.fromstring(
        xml
    )

    root_name = _local_name(
        root.tag
    )

    if root_name == "sitemapindex":
        sitemap_urls: list[str] = []

        for item in root:
            if _local_name(
                item.tag
            ) != "sitemap":
                continue

            for child in item:
                if _local_name(
                    child.tag
                ) != "loc":
                    continue

                if child.text:
                    value = child.text.strip()

                    if value:
                        sitemap_urls.append(
                            value
                        )

                break

        return SitemapLocationDocument(
            source_type="sitemap_index",
            sitemap_urls=sitemap_urls,
            page_urls=[],
        )

    if root_name == "urlset":
        page_urls: list[str] = []

        for item in root:
            if _local_name(
                item.tag
            ) != "url":
                continue

            for child in item:
                if _local_name(
                    child.tag
                ) != "loc":
                    continue

                if child.text:
                    value = child.text.strip()

                    if value:
                        page_urls.append(
                            value
                        )

                break

        return SitemapLocationDocument(
            source_type="sitemap",
            sitemap_urls=[],
            page_urls=page_urls,
        )

    raise ValueError(
        f"Unsupported sitemap root element: {root_name}"
    )

# ----------------------------------------------------------------------
# Rich parser.
#
# Keep this for sitemap evidence, catalog construction and any use case
# where sitemap metadata itself matters.
# ----------------------------------------------------------------------
def parse_sitemap_xml(
    *,
    xml: str,
    source_url: str,
) -> SitemapTopology:
    root = ET.fromstring(
        xml
    )

    root_name = _local_name(
        root.tag
    )

    if root_name == "sitemapindex":
        return _parse_sitemap_index(
            root=root,
            source_url=source_url,
        )

    if root_name == "urlset":
        return _parse_urlset(
            root=root,
            source_url=source_url,
        )

    raise ValueError(
        f"Unsupported sitemap root element: {root_name}"
    )


def _parse_sitemap_index(
    *,
    root: ET.Element,
    source_url: str,
) -> SitemapTopology:
    partitions: list[
        SitemapPartition
    ] = []

    for item in root:
        if _local_name(
            item.tag
        ) != "sitemap":
            continue

        loc = _child_text(
            item,
            "loc",
            namespace=SITEMAP_NS,
        )

        if not loc:
            continue

        partitions.append(
            SitemapPartition(
                url=loc.strip(),
                entries=[],
                entry_count=0,
            )
        )

    return SitemapTopology(
        root_url=source_url,
        source_type="sitemap_index",
        partitions=partitions,
        total_urls=0,
    )


def _parse_urlset(
    *,
    root: ET.Element,
    source_url: str,
) -> SitemapTopology:
    entries: list[
        SitemapEntry
    ] = []

    for item in root:
        if _local_name(
            item.tag
        ) != "url":
            continue

        entry = _parse_url_entry(
            item
        )

        if entry is None:
            continue

        entries.append(
            entry
        )

    partition = SitemapPartition(
        url=source_url,
        entries=entries,
        entry_count=len(
            entries
        ),
    )

    return SitemapTopology(
        root_url=source_url,
        source_type="sitemap",
        partitions=[
            partition
        ],
        total_urls=len(
            entries
        ),
    )


def _parse_url_entry(
    element: ET.Element,
) -> SitemapEntry | None:
    loc = _child_text(
        element,
        "loc",
        namespace=SITEMAP_NS,
    )

    if not loc:
        return None

    last_modified = _parse_datetime(
        _child_text(
            element,
            "lastmod",
            namespace=SITEMAP_NS,
        )
    )

    change_frequency = _clean_text(
        _child_text(
            element,
            "changefreq",
            namespace=SITEMAP_NS,
        )
    )

    priority = _parse_float(
        _child_text(
            element,
            "priority",
            namespace=SITEMAP_NS,
        )
    )

    resources = [
        *_parse_image_resources(
            element
        ),
        *_parse_video_resources(
            element
        ),
    ]

    return SitemapEntry(
        url=loc.strip(),
        last_modified=last_modified,
        change_frequency=change_frequency,
        priority=priority,
        resources=resources,
        metadata={},
    )


def _parse_image_resources(
    element: ET.Element,
) -> list[SitemapResource]:
    resources: list[
        SitemapResource
    ] = []

    for image in element:
        if not _is_tag(
            image.tag,
            namespace=IMAGE_NS,
            local_name="image",
        ):
            continue

        url = _child_text(
            image,
            "loc",
            namespace=IMAGE_NS,
        )

        if not url:
            continue

        url = url.strip()

        resources.append(
            SitemapResource(
                url=url,
                kind="image",
                format=_extension_from_url(
                    url
                ),
                title=_clean_text(
                    _child_text(
                        image,
                        "title",
                        namespace=IMAGE_NS,
                    )
                ),
                caption=_clean_text(
                    _child_text(
                        image,
                        "caption",
                        namespace=IMAGE_NS,
                    )
                ),
                metadata={
                    key: value
                    for key, value in {
                        "license": _clean_text(
                            _child_text(
                                image,
                                "license",
                                namespace=IMAGE_NS,
                            )
                        ),
                        "geo_location": _clean_text(
                            _child_text(
                                image,
                                "geo_location",
                                namespace=IMAGE_NS,
                            )
                        ),
                    }.items()
                    if value is not None
                },
            )
        )

    return resources


def _parse_video_resources(
    element: ET.Element,
) -> list[SitemapResource]:
    resources: list[
        SitemapResource
    ] = []

    for video in element:
        if not _is_tag(
            video.tag,
            namespace=VIDEO_NS,
            local_name="video",
        ):
            continue

        content_url = _clean_text(
            _child_text(
                video,
                "content_loc",
                namespace=VIDEO_NS,
            )
        )

        player_url = _clean_text(
            _child_text(
                video,
                "player_loc",
                namespace=VIDEO_NS,
            )
        )

        thumbnail_url = _clean_text(
            _child_text(
                video,
                "thumbnail_loc",
                namespace=VIDEO_NS,
            )
        )

        resource_url = (
            content_url
            or player_url
        )

        if not resource_url:
            continue

        metadata = {
            key: value
            for key, value in {
                "player_url": player_url,
                "duration_seconds": _parse_int(
                    _child_text(
                        video,
                        "duration",
                        namespace=VIDEO_NS,
                    )
                ),
                "publication_date": _clean_text(
                    _child_text(
                        video,
                        "publication_date",
                        namespace=VIDEO_NS,
                    )
                ),
                "family_friendly": _clean_text(
                    _child_text(
                        video,
                        "family_friendly",
                        namespace=VIDEO_NS,
                    )
                ),
                "live": _clean_text(
                    _child_text(
                        video,
                        "live",
                        namespace=VIDEO_NS,
                    )
                ),
            }.items()
            if value is not None
        }

        resources.append(
            SitemapResource(
                url=resource_url,
                kind="video",
                format=_extension_from_url(
                    resource_url
                ),
                title=_clean_text(
                    _child_text(
                        video,
                        "title",
                        namespace=VIDEO_NS,
                    )
                ),
                description=_clean_text(
                    _child_text(
                        video,
                        "description",
                        namespace=VIDEO_NS,
                    )
                ),
                thumbnail_url=thumbnail_url,
                metadata=metadata,
            )
        )

    return resources


def _child_text(
    element: ET.Element,
    name: str,
    *,
    namespace: str | None = None,
) -> str | None:
    for child in element:
        if (
            namespace is not None
            and not _is_tag(
                child.tag,
                namespace=namespace,
                local_name=name,
            )
        ):
            continue

        if (
            namespace is None
            and _local_name(
                child.tag
            ) != name
        ):
            continue

        return child.text

    return None


def _is_tag(
    tag: str,
    *,
    namespace: str,
    local_name: str,
) -> bool:
    return (
        tag
        == f"{{{namespace}}}{local_name}"
    )


def _local_name(
    tag: str,
) -> str:
    return tag.rsplit(
        "}",
        1,
    )[-1]


def _clean_text(
    value: str | None,
) -> str | None:
    if value is None:
        return None

    cleaned = value.strip()

    return (
        cleaned
        or None
    )


def _parse_datetime(
    value: str | None,
) -> datetime | None:
    value = _clean_text(
        value
    )

    if not value:
        return None

    try:
        return datetime.fromisoformat(
            value.replace(
                "Z",
                "+00:00",
            )
        )
    except ValueError:
        return None


def _parse_float(
    value: str | None,
) -> float | None:
    value = _clean_text(
        value
    )

    if not value:
        return None

    try:
        return float(
            value
        )
    except ValueError:
        return None


def _parse_int(
    value: str | None,
) -> int | None:
    value = _clean_text(
        value
    )

    if not value:
        return None

    try:
        return int(
            value
        )
    except ValueError:
        return None


def _extension_from_url(
    url: str,
) -> str | None:
    parsed = urlparse(
        url
    )

    suffix = PurePosixPath(
        parsed.path
    ).suffix.lower()

    if not suffix:
        return None

    return suffix.lstrip(
        "."
    )