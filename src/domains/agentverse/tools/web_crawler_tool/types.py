from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from src.domains.agentverse.tools.web_crawler_tool.auth.context import (
    CrawlAuthContext,
)
from src.domains.agentverse.tools.web_crawler_tool.constants import (
    CrawlGraphEdgeStatus,
    CrawlGraphNodeStatus,
    SecurityAuditDecision,
    SecurityAuditStage,
)


class PreparedCrawlExecution(BaseModel):
    start_url: str
    crawl_mode: str
    auth_context: CrawlAuthContext | None

class CrawlerReportContract(BaseModel):
    contract_version: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class CrawlAcquisitionProgress(BaseModel):
    current: int
    limit: int

    successful: int = 0
    failed: int = 0

    queued: int = 0
    visited: int = 0

    current_url: str | None = None
    depth: int | None = None

    metadata: dict[str, Any] = Field(
        default_factory=dict
    )


class DocumentInventoryItem(BaseModel):
    url: str
    source_page: str = ""
    anchor_text: str = ""
    context_text: str = ""

    document_type: str = "unknown"  # pdf | word | spreadsheet | presentation | text | data | unknown
    mime_hint: str | None = None

    is_pdf: bool = False
    is_download: bool = False
    is_internal: bool = False
    is_external: bool = False

    domain: str | None = None
    path: str = ""
    filename: str | None = None
    stem: str | None = None
    extension: str | None = None
    evidence_role: str = "document_candidate"

    source_field: str = "links"
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentInventoryReport(BaseModel):
    contract_version: str = "crawler.documents.v1"
    document_count: int = 0
    pdf_count: int = 0
    word_count: int = 0
    spreadsheet_count: int = 0
    presentation_count: int = 0
    text_count: int = 0
    data_count: int = 0
    internal_documents: int = 0
    external_documents: int = 0
    unique_domains: list[str] = Field(default_factory=list)
    items: list[DocumentInventoryItem] = Field(default_factory=list)


class MediaInventoryItem(BaseModel):
    src: str
    source_page: str = ""
    kind: str = "image"  # image | video | iframe | background | source | unknown

    alt: str | None = None
    title: str | None = None
    width: str | int | None = None
    height: str | int | None = None
    loading: str | None = None

    role: str = "unknown"  # hero_candidate | logo | icon | product_candidate | content | decorative | background | video | embed | unknown
    domain: str | None = None
    path: str = ""
    filename: str | None = None
    stem: str | None = None
    extension: str | None = None

    is_image: bool = False
    is_video: bool = False
    is_iframe: bool = False
    is_background: bool = False
    is_logo: bool = False
    is_icon: bool = False
    is_svg: bool = False
    is_lazy_loaded: bool = False
    is_external: bool = False
    is_download_candidate: bool = False

    occurrences: int = 1
    found_in_pages: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PageMediaInventoryReport(BaseModel):
    contract_version: str = "crawler.media.page.v1"

    total_media: int = 0
    image_count: int = 0
    video_count: int = 0
    iframe_count: int = 0
    background_image_count: int = 0
    svg_count: int = 0
    external_media_count: int = 0
    lazy_loaded_count: int = 0
    logo_candidates: int = 0
    icon_candidates: int = 0
    hero_candidates: int = 0
    product_candidates: int = 0
    unique_domains: list[str] = Field(default_factory=list)
    items: list[MediaInventoryItem] = Field(default_factory=list)


class SiteMediaInventoryReport(BaseModel):
    contract_version: str = "crawler.media.site.v1"

    total_media: int = 0
    unique_media: int = 0
    pages_with_media: int = 0
    image_count: int = 0
    video_count: int = 0
    iframe_count: int = 0
    background_image_count: int = 0
    svg_count: int = 0
    external_media_count: int = 0
    lazy_loaded_count: int = 0
    logo_candidates: int = 0
    icon_candidates: int = 0
    hero_candidates: int = 0
    product_candidates: int = 0
    unique_domains: list[str] = Field(default_factory=list)
    items: list[MediaInventoryItem] = Field(default_factory=list)


class ContentBlockLink(BaseModel):
    text: str = ""
    href: str = ""


class ContentBlockMedia(BaseModel):
    kind: str = "image"  # image | video | iframe | svg | unknown
    src: str | None = None
    alt: str | None = None
    title: str | None = None


class ContentBlockItem(BaseModel):
    index: int
    page_url: str = ""

    block_type: str = "content"  # main | content | article | section | quote | header | cta | unknown

    heading: str = ""
    heading_level: str | None = None

    text: str = ""
    text_len: int = 0

    evidence_score: float = 0.0
    evidence_role: str = "content_candidate"
    # primary_text_evidence | supporting_text_evidence | low_priority_text | navigation_text | footer_text

    source: dict[str, Any] = Field(default_factory=dict)


class ContentBlocksReport(BaseModel):
    contract_version: str = "crawler.content_blocks.v1"

    block_count: int = 0
    total_text_len: int = 0
    primary_evidence_blocks: int = 0
    supporting_evidence_blocks: int = 0
    low_priority_blocks: int = 0
    text_heavy_blocks: int = 0
    combined_text: str = ""
    blocks: list[ContentBlockItem] = Field(default_factory=list)


class DetectedHTMLBlockItem(BaseModel):
    index: int
    page_url: str = ""

    block_type: str = "unknown"
    tag: str = ""
    id: str | None = None
    classes: list[str] = Field(default_factory=list)
    role: str | None = None

    heading: str = ""
    heading_level: str | None = None

    text_preview: str = ""
    text_len: int = 0

    link_count: int = 0
    media_count: int = 0
    links: list[ContentBlockLink] = Field(default_factory=list)
    media: list[ContentBlockMedia] = Field(default_factory=list)

    signals: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DetectedHTMLBlocksReport(BaseModel):
    contract_version: str = "crawler.detected_html_blocks.v1"
    block_count: int = 0

    hero_blocks: int = 0
    navigation_blocks: int = 0
    cta_blocks: int = 0
    card_blocks: int = 0
    media_blocks: int = 0
    quote_blocks: int = 0
    footer_blocks: int = 0
    article_blocks: int = 0
    section_blocks: int = 0
    main_blocks: int = 0

    text_heavy_blocks: int = 0

    by_type: dict[str, int] = Field(default_factory=dict)
    by_tag: dict[str, int] = Field(default_factory=dict)
    signal_counts: dict[str, int] = Field(default_factory=dict)

    blocks: list[DetectedHTMLBlockItem] = Field(default_factory=list)


class CrawlGraphNode(BaseModel):
    url: str
    depth: int = 0
    status: CrawlGraphNodeStatus = "queued"
    title: str = ""
    status_code: int | None = None
    content_type: str | None = None
    error: str | None = None
    discovered_from: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CrawlGraphEdge(BaseModel):
    source_url: str
    target_url: str
    depth: int = 0
    status: CrawlGraphEdgeStatus = "discovered"
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CrawlGraphReport(BaseModel):
    contract_version: str = "crawler.crawl_graph.v1"

    node_count: int = 0
    edge_count: int = 0
    visited_count: int = 0
    queued_count: int = 0
    skipped_count: int = 0
    blocked_count: int = 0
    error_count: int = 0
    max_depth_seen: int = 0
    internal_edge_count: int = 0
    external_edge_count: int = 0
    duplicate_edge_count: int = 0
    nodes: list[CrawlGraphNode] = Field(default_factory=list)
    edges: list[CrawlGraphEdge] = Field(default_factory=list)


class FormControlItem(BaseModel):
    tag: str
    type: str | None = None
    name: str | None = None
    id: str | None = None
    label: str | None = None
    placeholder: str | None = None
    aria_label: str | None = None
    required: bool = False
    disabled: bool = False
    autocomplete: str | None = None
    value_present: bool = False
    options_count: int = 0
    control_role: str = "unknown"
    risk_flags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FormInventoryItem(BaseModel):
    index: int
    source_url: str
    method: str = "get"
    action: str | None = None
    action_url: str | None = None
    target: str | None = None
    enctype: str | None = None
    form_role: str = "unknown"
    control_count: int = 0
    required_count: int = 0
    unlabeled_count: int = 0
    sensitive_count: int = 0
    risk_flags: list[str] = Field(default_factory=list)
    controls: list[FormControlItem] = Field(default_factory=list)


class FormInventoryReport(BaseModel):
    contract_version: str = "crawler.forms.v1"

    form_count: int = 0
    total_controls: int = 0
    required_controls: int = 0
    unlabeled_controls: int = 0
    sensitive_controls: int = 0
    search_forms: int = 0
    auth_forms: int = 0
    newsletter_forms: int = 0
    contact_forms: int = 0
    file_upload_forms: int = 0
    external_action_forms: int = 0
    forms: list[FormInventoryItem] = Field(default_factory=list)


class LinkInventoryItem(BaseModel):
    url: str
    text: str = ""
    source_url: str = ""
    link_type: str = "unknown"
    scope: str = "unknown"  # internal | external | same_page | special
    domain: str | None = None
    path: str = ""
    scheme: str = ""
    is_download: bool = False
    is_social: bool = False
    is_mailto: bool = False
    is_tel: bool = False
    is_cta_candidate: bool = False
    source_field: str = "links"
    metadata: dict[str, Any] = Field(default_factory=dict)


class LinkInventoryReport(BaseModel):
    contract_version: str = "crawler.links.v1"

    metadata: dict[str, Any] = Field(default_factory=dict)
    total_links: int = 0
    unique_links: int = 0
    internal_links: int = 0
    external_links: int = 0
    same_page_links: int = 0
    special_links: int = 0
    download_links: int = 0
    social_links: int = 0
    mailto_links: int = 0
    tel_links: int = 0
    cta_candidates: int = 0
    unique_domains: list[str] = Field(default_factory=list)
    items: list[LinkInventoryItem] = Field(default_factory=list)

class CrawlerTimingMetadata(BaseModel):
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: float | None = None
    duration_seconds: float | None = None
    timezone: str = "UTC"


class CrawlerLimitMetadata(BaseModel):
    max_pages: int = 0
    max_depth: int = 0
    timeout_seconds: int = 0
    max_chars_per_page: int = 0


class CrawlerFlagMetadata(BaseModel):
    same_domain_only: bool = True
    include_links: bool = True
    include_raw_pages: bool = False
    include_security_audit: bool = False
    include_errors: bool = True


class CrawlerFindMetadata(BaseModel):
    query: str | None = None
    case_sensitive: bool = False
    max_matches: int = 20
    find_scope: str = "all"
    find_report: str | None = None

class CrawlerEvidenceMetadata(BaseModel):
    origin: str = "unknown"
    reused: bool = False

    snapshot_id: str | None = None
    snapshot_created_at: str | None = None
    snapshot_age_seconds: float | None = None

    acquisition_fingerprint: str | None = None

class CrawlerCoverageMetadata(BaseModel):
    checked: bool = False
    complete: bool = False

    reusable_count: int = 0
    stale_count: int = 0
    missing_count: int = 0
    frontier_count: int = 0

    acquisition_candidates: int = 0
    planned_acquisition_count: int = 0

    plan_truncated: bool = False

    reasons: list[str] = Field(
        default_factory=list
    )


class CrawlerMetadataReport(BaseModel):
    contract_version: str = "crawler.metadata.v1"

    start_url: str
    mode: str

    output_profile: str | None = None
    output_format: str = "markdown"
    report: str | list[str] | None = None
    requested_reports: list[str] = Field(
        default_factory=list
    )
    crawl_strategy: str = "broad"

    timing: CrawlerTimingMetadata = Field(
        default_factory=CrawlerTimingMetadata
    )

    limits: CrawlerLimitMetadata = Field(
        default_factory=CrawlerLimitMetadata
    )

    flags: CrawlerFlagMetadata = Field(
        default_factory=CrawlerFlagMetadata
    )

    filters: dict[str, Any] = Field(
        default_factory=dict
    )

    find: CrawlerFindMetadata = Field(
        default_factory=CrawlerFindMetadata
    )

    coverage: CrawlerCoverageMetadata = Field(
        default_factory=CrawlerCoverageMetadata
    )



class CrawlMatch(BaseModel):
    page_url: str
    page_title: str = ""
    field: str = "text"
    query: str
    match_text: str
    before: str = ""
    after: str = ""
    score: float | None = None


class CrawlResult(BaseModel):
    start_url: str
    mode: str
    page_count: int
    pages: list[CrawledPage]
    errors: list[str] = Field(default_factory=list)
    security_audit: list[SecurityAuditEntry] = Field(default_factory=list)
    crawl_graph: CrawlGraphReport | None = None

class CrawlerMeta(BaseModel):
    contract_version: str = "crawler.meta.v1"

    start_url: str
    mode: str
    page_count: int
    report: str | None = None
    find: str | None = None
    match_count: int = 0
    error_count: int = 0
    rule_id: str | None = None

class CrawledPage(BaseModel):
    url: str
    final_url: str | None = None
    status_code: int | None = None
    content_type: str | None = None
    title: str = ""
    text: str = ""
    links: list[str] = Field(default_factory=list)
    html_report: dict[str, Any] | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    reports: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class SecurityAuditEntry(BaseModel):
    url: str
    decision: SecurityAuditDecision
    stage: SecurityAuditStage
    reason: str
    category: str = "unknown"
    rule_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class CrawlerResultAddons(BaseModel):
    crawler_meta: CrawlerMeta
    errors: list[str] = Field(default_factory=list)
    security_audit: list[SecurityAuditEntry] = Field(default_factory=list)


class CrawlerToolResult(BaseModel):
    """
    Standard AgentVerse crawler result contract.

    result.answer:
        Human-readable markdown report for chat and LLM handoff.

    result.pages:
        Raw crawled page content and links.

    result.components:
        Structured HTML/component reports extracted from pages.

    result.addons:
        Metadata, diagnostics, errors, and execution details.
    """
    contract_version: str = "crawler.tool_result.v1"

    answer: str
    pages: list[CrawledPage] = Field(default_factory=list)
    components: list[dict] = Field(default_factory=list)
    matches: list[CrawlMatch] = Field(default_factory=list)
    addons: CrawlerResultAddons
    errors: list[str] = Field(default_factory=list)
    security_audit: list[SecurityAuditEntry] = Field(default_factory=list)
    reports: dict[str, Any] = Field(default_factory=dict)