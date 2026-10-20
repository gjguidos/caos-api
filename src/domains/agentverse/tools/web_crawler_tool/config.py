from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from src.domains.agentverse.tools.web_crawler_tool.constants import MAX_CRAWL_PAGES
from src.domains.agentverse.tools.web_crawler_tool.helpers.resources import (
    normalize_extension_list as _normalize_extension_list,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.models import (
    SitemapSelection,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.types import (
    SitemapDiscoveryRequestMode,
    SitemapTraversalPolicy,
)


class WebCrawlerToolParams(BaseModel):
    url: str
    crawl_scope: Literal[
        "page",
        "site",
        "sitemap",
    ] = "site"
    # --------------------------------------------------------------
    # Sitemap semantics
    # --------------------------------------------------------------

    sitemap_discovery_mode: (
        SitemapDiscoveryRequestMode | None
    ) = None

    sitemap_selection: (
        SitemapSelection | None
    ) = None

    sitemap_traversal_policy: (
        SitemapTraversalPolicy | None
    ) = None

    max_sitemaps: int | None = Field(
        default=None,
        ge=1,
    )

    page_acquisition_limit: int | None = Field(
        default=None,
        ge=1,
    )
    mode: Literal["static", "browser", "auto"] | None = None
    crawl_strategy: Literal["broad", "deep"] | None = None
    auth_mode: Literal[
        "none",
        "delegated_entra",
        "delegated_session",
        "browser_context",
    ] | None = None

    browser_session_id: str | None = None

    max_pages: int | None = Field(
        default=None,
        ge=1,
        le=MAX_CRAWL_PAGES,
    )
    max_depth: int | None = Field(default=None, ge=0, le=5)
    timeout_seconds: int | None = Field(default=None, ge=1, le=120)
    max_chars_per_page: int | None = Field(
        default=None,
        ge=500,
        le=100_000,
    )

    output_profile: str | None = None
    output_format: Literal["markdown", "json"] | None = None

    report: str | list[str] | None = None

    same_domain_only: bool | None = None
    include_links: bool | None = None
    include_raw_pages: bool | None = None
    include_security_audit: bool | None = None
    include_errors: bool | None = None

    find: str | None = None
    find_scope: Literal["page", "reports", "all"] | None = None
    find_report: str | None = None
    case_sensitive: bool | None = None
    max_matches: int | None = Field(
        default=None,
        ge=1,
        le=500,
    )

    media_extensions: list[str] = Field(default_factory=list)
    media_filename: str | None = None
    media_role: str | None = None

    document_extensions: list[str] = Field(default_factory=list)
    document_filename: str | None = None
    document_type: str | None = None

    context: dict[str, object] | None = None

    @field_validator(
        "media_extensions",
        "document_extensions",
        mode="before",
    )
    @classmethod
    def normalize_extensions(
        cls,
        value: Any,
    ) -> list[str]:
        return _normalize_extension_list(value)


class WebCrawlerSecurityConfig(BaseModel):
    mode: Literal["strict", "balanced", "permissive"] = "strict"

    allowed_domains: list[str] = Field(default_factory=list)
    blocked_domains: list[str] = Field(default_factory=list)

    block_private_networks: bool = True
    block_localhost: bool = True
    block_cloud_metadata: bool = True
    block_internal_hostnames: bool = True

    block_inappropriate_sites: bool = True
    blocked_content_categories: list[str] = Field(
        default_factory=lambda: [
            "inappropriate_site",
            "adult",
            "pornography",
            "sexual_content",
            "gambling",
            "illegal_drugs",
            "weapons",
            "extremism",
            "malware",
            "phishing",
            "piracy",
            "hate",
            "self_harm",
            "violence_graphic",
        ]
    )

    blocked_url_keywords: list[str] = Field(default_factory=list)

    max_redirects: int = Field(default=5, ge=0, le=20)
    block_cross_domain_redirects: bool = False

    max_response_bytes: int = Field(
        default=5_000_000,
        ge=100_000,
        le=50_000_000,
    )
    allowed_content_types: list[str] = Field(
        default_factory=lambda: [
            "text/html",
            "text/plain",
            "application/xhtml+xml",
        ]
    )

    audit_security_decisions: bool = True


class WebCrawlerToolConfig(BaseModel):
    enabled: bool = True
    debug: bool = False
    dry_run: bool = False
    retries: int = 0
    retry_backoff: float = 0.5
    configurable: bool = True

    provider: str = "tools:web"
    crawl_scope: Literal[
        "page",
        "site",
        "sitemap",
    ] = "site"
    sitemap_discovery_mode: (
        SitemapDiscoveryRequestMode
    ) = "planned"
    sitemap_traversal_policy: (
        SitemapTraversalPolicy
    ) = "complete"

    # Optional organisation-level safety ceilings.
    #
    # None means that this deployment has not configured a hard
    # ceiling. Operational bounding still comes from chunking and
    # concurrency.
    sitemap_max_sitemaps_limit: int | None = Field(
        default=None,
        ge=1,
    )

    sitemap_page_acquisition_limit_max: int | None = Field(
        default=None,
        ge=1,
    )
    mode: Literal["static", "browser", "auto"] = "static"
    crawl_strategy: Literal["broad", "deep"] = "broad"
    auth_mode: Literal[
        "none",
        "delegated_entra",
        "delegated_session",
        "browser_context",
    ] = "none"
    include_links: bool = True
    default_max_pages: int = Field(
        default=3,
        ge=1,
        le=MAX_CRAWL_PAGES,
    )

    default_max_depth: int = Field(
        default=1,
        ge=0,
        le=5,
    )
    max_pages_limit: int = Field(
        default=MAX_CRAWL_PAGES,
        ge=1,
        le=MAX_CRAWL_PAGES,
    )

    max_depth_limit: int = Field(
        default=5,
        ge=0,
        le=5,
    )

    timeout_seconds: int = Field(default=15, ge=1, le=120)
    timeout: float | None = None
    max_chars_per_page: int = Field(default=8_000, ge=500, le=100_000)

    same_domain_only: bool = True
    respect_robots_txt: bool = True

    allow_browser_mode: bool = True
    allow_private_networks: bool = False
    allow_localhost: bool = False

    rate_limit_delay_ms: int = 0

    user_agent: str | None = None
    debug_outputs: bool = False

    crawler_workers: int = Field(
        default=6,
        ge=0,
        le=6
    )

    report_workers: int = Field(
        default=6,
        ge=0,
        le=22
    )
    
    sitemap_max_response_bytes: int = Field(
        default=50 * 1024 * 1024,
        ge=1 * 1024 * 1024,
        le=100 * 1024 * 1024,
    )

    sitemap_state_ttl_seconds: int = Field(
        default=1800,
        ge=60,
        le=86_400,
    )

    sitemap_freshness_ttl_seconds: int = Field(
        default=300,  # 5 minutes
        ge=60,
        le=86_400,
    )

    sitemap_discovery_concurrency: int = Field(
        default=8,
        ge=1,
        le=32,
    )

    sitemap_parse_concurrency: int = Field(
        default=4,
        ge=2,
        le=16,
    )

    sitemap_cpu_concurrency: int = Field(
        default=4,
        ge=1,
        le=8,
    )

    snapshot_ttl_seconds: int = Field(
        default=1800,
        ge=60,
        le=86_400,
    )

    site_state_ttl_seconds: int = Field(
        default=604_800,  # 7 days
        ge=3_600,
        le=2_592_000,  # 30 days
    )

    page_freshness_ttl_seconds: int = Field(
        default=3600,
        ge=60,
        le=604_800,  # 7 days
    )

    crawl_plan_chunk_size: int = Field(
        default=100,
        ge=1,
        le=10_000,
    )

    max_in_flight_chunks: int = Field(
        default=2,
        ge=1,
        le=4,
    )

    page_acquisition_concurrency: int = Field(
        default=25,
        ge=1,
        le=25,
    )

    page_acquisition_batch_size: int = Field(
        default=100,
        ge=1,
        le=1_000,
    )

    use_queue_acquisition: bool = True
    

    browser_fallback_enabled: bool = True
    fallback_min_text_chars: int = Field(default=300, ge=0, le=5_000)
    fallback_min_links: int = Field(default=1, ge=0, le=100)
    fallback_min_headings: int = Field(default=1, ge=0, le=100)
    fallback_score_threshold: int = Field(default=5, ge=1, le=20)

    browser_wait_until: str = "domcontentloaded"
    browser_wait_ms: int = Field(default=2_500, ge=0, le=15_000)
    browser_viewport_width: int = Field(default=1_366, ge=320, le=3_840)
    browser_viewport_height: int = Field(default=768, ge=320, le=2_160)
    browser_block_resources: bool = False
    browser_allowed_resource_types: list[str] = Field(
        default_factory=lambda: ["document", "xhr", "fetch", "script"]
    )

    security: WebCrawlerSecurityConfig = Field(
        default_factory=WebCrawlerSecurityConfig
    )



