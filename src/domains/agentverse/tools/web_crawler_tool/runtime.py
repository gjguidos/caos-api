from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.tools.base import ToolExecutionError
from src.domains.agentverse.tools.web_crawler_tool.config import (
    WebCrawlerToolConfig,
    WebCrawlerToolParams,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.models import (
    SitemapSelection,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.types import (
    SitemapDiscoveryMode,
    SitemapDiscoveryRequestMode,
    SitemapTraversalPolicy,
)


@dataclass(frozen=True)
class ResolvedCrawlerRuntime:
    params: WebCrawlerToolParams

    crawl_scope: str
    sitemap_discovery_mode: SitemapDiscoveryMode
    sitemap_traversal_policy: SitemapTraversalPolicy
    max_sitemaps: int | None
    page_acquisition_limit: int | None

    report: str
    output_format: str
    include_raw_pages: bool
    include_security_audit: bool
    include_errors: bool
    output_profile: str

    max_pages: int
    max_depth: int
    crawl_strategy: str
    include_links: bool
    same_domain_only: bool
    timeout_seconds: int
    max_chars_per_page: int

    crawl_mode: str
    auth_mode: str

    request_overrides: dict[str, Any]
    profile_options: dict[str, Any]


def normalize_crawler_runtime(
    *,
    cfg: WebCrawlerToolConfig,
    profile_resolver: Callable[..., dict[str, Any]],
    url: str,
    crawl_scope: str | None = None,
    sitemap_discovery_mode: SitemapDiscoveryRequestMode | None = None,
    sitemap_selection: SitemapSelection | None = None,
    sitemap_traversal_policy: (
        SitemapTraversalPolicy | None
    ) = None,

    max_sitemaps: int | None = None,

    page_acquisition_limit: (
        int | None
    ) = None,
    mode: str | None = None,
    auth_mode: str | None = None,
    browser_session_id: str | None = None,
    crawl_strategy: str | None = None,
    include_links: bool | None = None,
    report: str | list[str] | None = None,
    output_profile: str | None = None,
    output_format: str | None = None,
    include_raw_pages: bool | None = None,
    include_security_audit: bool | None = None,
    include_errors: bool | None = None,
    find: str | None = None,
    find_scope: str = "all",
    find_report: str | None = None,
    media_extensions: list[str] | None = None,
    media_filename: str | None = None,
    media_role: str | None = None,
    document_extensions: list[str] | None = None,
    document_filename: str | None = None,
    document_type: str | None = None,
    case_sensitive: bool = False,
    max_matches: int = 20,
    max_pages: int | None = None,
    max_depth: int | None = None,
    same_domain_only: bool | None = None,
    max_chars_per_page: int | None = None,
    timeout_seconds: int | None = None,
    context: dict[str, Any] | None = None,
) -> ResolvedCrawlerRuntime:
    
    params = WebCrawlerToolParams(
        url=url,
        crawl_scope=crawl_scope,
        sitemap_discovery_mode=sitemap_discovery_mode,
        sitemap_selection=sitemap_selection,
        sitemap_traversal_policy=(
            sitemap_traversal_policy
        ),

        max_sitemaps=max_sitemaps,

        page_acquisition_limit=(
            page_acquisition_limit
        ),
        mode=mode,
        auth_mode=auth_mode,
        browser_session_id=browser_session_id,
        crawl_strategy=crawl_strategy,
        include_links=include_links,
        report=report,
        output_profile=output_profile,
        output_format=output_format,
        include_raw_pages=include_raw_pages,
        include_security_audit=include_security_audit,
        include_errors=include_errors,
        find=find,
        find_scope=find_scope,
        find_report=find_report,
        media_extensions=media_extensions,
        media_filename=media_filename,
        media_role=media_role,
        document_extensions=document_extensions,
        document_filename=document_filename,
        document_type=document_type,
        case_sensitive=case_sensitive,
        max_matches=max_matches,
        max_pages=max_pages,
        max_depth=max_depth,
        same_domain_only=same_domain_only,
        max_chars_per_page=max_chars_per_page,
        timeout_seconds=timeout_seconds,
        context=context,
    )
    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        (
            "[🕷️ WEB CRAWLER]"
            "[RUNTIME_INPUT] "
            f"sitemap_traversal_policy="
            f"{sitemap_traversal_policy!r} "
            f"max_sitemaps={max_sitemaps!r} "
        ),
    )

    request_overrides = build_profile_request_overrides(params)

    profile_options = profile_resolver(
        output_profile=params.output_profile,
        request_overrides=request_overrides,
    )

    resolved_max_pages = int(
        first_not_none(
            profile_options.get("max_pages"),
            cfg.default_max_pages,
        )
    )

    if resolved_max_pages > cfg.max_pages_limit:
        raise ToolExecutionError(
            "Requested max_pages "
            f"({resolved_max_pages}) exceeds the configured "
            f"crawler limit ({cfg.max_pages_limit})."
        )

    resolved_max_depth = int(
        first_not_none(
            profile_options.get("max_depth"),
            cfg.default_max_depth,
        )
    )

    if resolved_max_depth > cfg.max_depth_limit:
        raise ToolExecutionError(
            "Requested max_depth "
            f"({resolved_max_depth}) exceeds the configured "
            f"crawler limit ({cfg.max_depth_limit})."
        )

    resolved_timeout_seconds = int(
        first_not_none(
            profile_options.get("timeout_seconds"),
            params.timeout_seconds,
            cfg.timeout_seconds,
        )
    )

    resolved_max_chars_per_page = int(
        first_not_none(
            profile_options.get("max_chars_per_page"),
            params.max_chars_per_page,
            cfg.max_chars_per_page,
        )
    )

    resolved_crawl_strategy = str(
        first_not_none(
            profile_options.get("crawl_strategy"),
            params.crawl_strategy,
            cfg.crawl_strategy,
        )
    ).strip().lower()

    resolved_crawl_strategy = normalize_crawl_strategy(resolved_crawl_strategy)

    resolved_include_links = bool(
        first_not_none(
            profile_options.get("include_links"),
            params.include_links,
            cfg.include_links,
        )
    )

    resolved_same_domain_only = bool(
        first_not_none(
            profile_options.get("same_domain_only"),
            params.same_domain_only,
            cfg.same_domain_only,
        )
    )

    resolved_crawl_mode = str(
        first_not_none(
            profile_options.get("mode"),
            params.mode,
            cfg.mode,
        )
    ).strip().lower()

    

    resolved_auth_mode = str(
        first_not_none(
            params.auth_mode,
            cfg.auth_mode,
        )
    ).strip().lower()

    resolved_crawl_scope = str(
    first_not_none(
            params.crawl_scope,
            cfg.crawl_scope,
        )
    ).strip().lower()

    

    resolved_crawl_scope = normalize_crawl_scope(
        resolved_crawl_scope
    )

    resolved_max_sitemaps: int | None = None
    resolved_page_acquisition_limit: int | None = None

    resolved_sitemap_traversal_policy: SitemapTraversalPolicy = (
        "complete"
    )

    if resolved_crawl_scope == "sitemap":
        explicit_traversal_policy = (
            params.sitemap_traversal_policy
        )

        if (
            explicit_traversal_policy is None
            and params.max_sitemaps is not None
        ):
            resolved_sitemap_traversal_policy = "bounded"
        else:
            resolved_sitemap_traversal_policy = (
                normalize_sitemap_traversal_policy(
                    str(
                        first_not_none(
                            explicit_traversal_policy,
                            cfg.sitemap_traversal_policy,
                        )
                    )
                )
            )

        resolved_max_sitemaps = params.max_sitemaps
        resolved_page_acquisition_limit = (
            params.page_acquisition_limit
        )

        if (
            resolved_sitemap_traversal_policy == "bounded"
            and resolved_max_sitemaps is None
        ):
            raise ToolExecutionError(
                "max_sitemaps is required when "
                "sitemap_traversal_policy='bounded'."
            )

        if (
            cfg.sitemap_page_acquisition_limit_max is not None
            and resolved_page_acquisition_limit is not None
            and resolved_page_acquisition_limit
            > cfg.sitemap_page_acquisition_limit_max
        ):
            raise ToolExecutionError(
                "Requested page_acquisition_limit "
                f"({resolved_page_acquisition_limit}) exceeds "
                "the configured sitemap page acquisition limit "
                f"({cfg.sitemap_page_acquisition_limit_max})."
            )
    else:
        resolved_sitemap_traversal_policy = "complete"
        resolved_max_sitemaps = None
        resolved_page_acquisition_limit = None

    resolved_sitemap_discovery_mode = (
        normalize_sitemap_discovery_mode(
            str(
                first_not_none(
                    profile_options.get(
                        "sitemap_discovery_mode"
                    ),
                    params.sitemap_discovery_mode,
                    cfg.sitemap_discovery_mode,
                )
            )
        )
)

    return ResolvedCrawlerRuntime(
        params=params,
        crawl_scope=resolved_crawl_scope,
        sitemap_discovery_mode=resolved_sitemap_discovery_mode,
        sitemap_traversal_policy=(
            resolved_sitemap_traversal_policy
        ),

        max_sitemaps=(
            resolved_max_sitemaps
        ),

        page_acquisition_limit=(
            resolved_page_acquisition_limit
        ),
        report=profile_options["report"],
        output_format=profile_options["output_format"],
        include_raw_pages=bool(
            profile_options["include_raw_pages"]
        ),
        include_security_audit=bool(
            profile_options["include_security_audit"]
        ),
        include_errors=bool(
            profile_options["include_errors"]
        ),
        output_profile=str(
            profile_options["output_profile"]
        ),
        max_pages=resolved_max_pages,
        max_depth=resolved_max_depth,
        crawl_strategy=resolved_crawl_strategy,
        include_links=resolved_include_links,
        same_domain_only=resolved_same_domain_only,
        timeout_seconds=resolved_timeout_seconds,
        max_chars_per_page=resolved_max_chars_per_page,
        crawl_mode=resolved_crawl_mode,
        auth_mode=resolved_auth_mode,
        request_overrides=request_overrides,
        profile_options=profile_options,
    )


def build_profile_request_overrides(
    params: WebCrawlerToolParams,
) -> dict[str, Any]:
    request_overrides: dict[str, Any] = {
        "output_format": params.output_format,
        "include_raw_pages": params.include_raw_pages,
        "include_security_audit": params.include_security_audit,
        "include_errors": params.include_errors,
        "max_pages": params.max_pages,
        "max_depth": params.max_depth,
        "include_links": params.include_links,
        "same_domain_only": params.same_domain_only,
        "mode": params.mode,
        "crawl_strategy": params.crawl_strategy,
        "sitemap_discovery_mode": params.sitemap_discovery_mode,
        "timeout_seconds": params.timeout_seconds,
        "max_chars_per_page": params.max_chars_per_page,
    }

    if has_meaningful_report(params.report):
        request_overrides["report"] = params.report

    return request_overrides


def normalize_crawl_strategy(value: str) -> str:
    normalized = value.strip().lower()

    if normalized in {"breadth_first", "breadth-first", "bfs"}:
        return "broad"

    if normalized in {"depth_first", "depth-first", "dfs"}:
        return "deep"

    if normalized not in {"broad", "deep"}:
        raise ToolExecutionError(
            f"Unsupported crawl_strategy: {value}. Use 'broad' or 'deep'."
        )

    return normalized


def first_not_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value

    return None


def has_meaningful_report(value: Any) -> bool:
    if value is None:
        return False

    if isinstance(value, str):
        stripped = value.strip()

        if not stripped:
            return False

        if stripped.lower() in {
            "none",
            "null",
            "undefined",
            "[]",
            "{}",
        }:
            return False

        if stripped.startswith("${") and stripped.endswith("}"):
            return False

        return True

    if isinstance(value, list | tuple | set):
        return any(has_meaningful_report(item) for item in value)

    if isinstance(value, dict):
        return bool(value)

    return True

def normalize_crawl_scope(
    value: str,
) -> str:
    normalized = value.strip().lower()

    if normalized not in {
        "page",
        "site",
        "sitemap",
    }:
        raise ToolExecutionError(
            "Unsupported crawl_scope: "
            f"{value}. Use 'page', 'site', or 'sitemap'."
        )

    return normalized

def normalize_sitemap_discovery_mode(
    value: str,
) -> SitemapDiscoveryMode:
    normalized = value.strip().lower()

    if normalized not in {
        "planned",
        "full",
    }:
        raise ToolExecutionError(
            "Unsupported sitemap_discovery_mode: "
            f"{value}. Use 'planned' or 'full'."
        )

    return cast(
        SitemapDiscoveryMode,
        normalized,
    )

def normalize_sitemap_traversal_policy(
    value: str,
) -> SitemapTraversalPolicy:
    normalized = (
        value.strip().lower()
    )

    if normalized not in {
        "complete",
        "bounded",
    }:
        raise ToolExecutionError(
            "Unsupported sitemap_traversal_policy: "
            f"{value}. Use 'complete' or 'bounded'."
        )

    return cast(
        SitemapTraversalPolicy,
        normalized,
    )