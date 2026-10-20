from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

SitemapResourceKind = Literal[
    "image",
    "video",
    "document",
    "other",
]

class SitemapResource(BaseModel):
    """
    Generic resource declared by a sitemap entry.

    This is the normalized representation used by downstream
    inventory/reporting code. Provider/site-specific concepts
    should not leak into this model.
    """

    url: str

    kind: SitemapResourceKind

    format: str | None = None

    title: str | None = None
    caption: str | None = None
    description: str | None = None

    thumbnail_url: str | None = None

    metadata: dict[str, Any] = Field(
        default_factory=dict
    )


class SitemapEntry(BaseModel):
    """
    One URL entry declared by a sitemap.

    Represents sitemap evidence only. It does not imply that the
    target page/resource has been fetched by AgentVerse.
    """

    url: str

    last_modified: datetime | None = None
    change_frequency: str | None = None
    priority: float | None = None

    resources: list[SitemapResource] = Field(
        default_factory=list
    )

    metadata: dict[str, Any] = Field(
        default_factory=dict
    )


class SitemapPartition(BaseModel):
    url: str

    entries: list[SitemapEntry] = Field(
        default_factory=list
    )

    entry_count: int = 0


class SitemapTopology(BaseModel):
    root_url: str

    source_type: Literal[
        "sitemap",
        "sitemap_index",
    ]

    partitions: list[SitemapPartition] = Field(
        default_factory=list
    )

    total_urls: int = 0


class SitemapPartitionDescriptor(BaseModel):
    url: str

    filename: str
    stem: str

    path_segments: list[str] = Field(
        default_factory=list
    )

    tokens: list[str] = Field(
        default_factory=list
    )

    numeric_suffix: int | None = None


class SitemapCatalogNode(BaseModel):
    token: str
    depth: int

    partition_count: int = 0

    children: list[
        SitemapCatalogNode
    ] = Field(
        default_factory=list
    )

    partition_urls: list[str] = Field(
        default_factory=list
    )


class SitemapCatalog(BaseModel):
    root_url: str

    partition_count: int = 0

    roots: list[
        SitemapCatalogNode
    ] = Field(
        default_factory=list
    )

class SitemapSelection(BaseModel):
    """
    Selection against a resolved SitemapCatalog.

    node_paths identifies structural catalog nodes, for example:

        ["prod", "sv", "SE"]

    Explicit partition URLs can also be supplied when needed.
    """

    node_paths: list[
        list[str]
    ] = Field(
        default_factory=list
    )

    partition_urls: list[str] = Field(
        default_factory=list
    )

class SitemapCoverageResult(BaseModel):
    discovered_urls: list[str] = Field(default_factory=list)
    reusable_urls: list[str] = Field(default_factory=list)
    stale_urls: list[str] = Field(default_factory=list)
    missing_urls: list[str] = Field(default_factory=list)

class SitemapResourceState(BaseModel):
    url: str
    final_url: str

    acquired_at: datetime

    etag: str | None = None
    last_modified: str | None = None

    topology: SitemapTopology

class SitemapInventory(BaseModel):
    entry_count: int = 0
    unique_entry_count: int = 0

    resource_count: int = 0
    unique_resource_count: int = 0

    entries_with_resources: int = 0
    entries_with_images: int = 0
    entries_with_videos: int = 0

    resources_by_kind: dict[str, int] = Field(
        default_factory=dict
    )

    resources_by_format: dict[str, int] = Field(
        default_factory=dict
    )

    resource_domains: dict[str, int] = Field(
        default_factory=dict
    )

class SitemapDiscoveryFailure(BaseModel):
    """
    Failure encountered while recursively resolving a sitemap tree.
    """

    url: str
    error: str

    parent_sitemap_url: str | None = None
    depth: int = 0


class SitemapDiscoveryResult(BaseModel):
    """
    Aggregated result of recursively resolving a sitemap tree.

    sitemap_urls:
        Every sitemap document encountered.

    page_urls:
        Normalized and deduplicated <loc> URLs from terminal
        sitemap/urlset documents.
    """

    root_url: str

    sitemap_urls: list[str] = Field(
        default_factory=list
    )

    page_urls: list[str] = Field(
        default_factory=list
    )

    sitemap_count: int = 0
    page_url_count: int = 0

    max_depth_reached: int = 0

    failed_sitemaps: list[
        SitemapDiscoveryFailure
    ] = Field(
        default_factory=list
    )

    failed_sitemap_count: int = 0
    

class SitemapPlanningCoverage(BaseModel):
    discovered_url_count: int = 0
    reusable_url_count: int = 0
    stale_url_count: int = 0
    missing_url_count: int = 0

    stale_candidates: list[str] = Field(
        default_factory=list
    )

    missing_candidates: list[str] = Field(
        default_factory=list
    )