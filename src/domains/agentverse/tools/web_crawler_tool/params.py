from __future__ import annotations

from typing import Any

parameters: dict[str, Any] = {
        # ---------------------------------------------------------------------
        # Target
        # ---------------------------------------------------------------------
        "url": {
            "type": "string",
            "required": True,
            "description": (
                "Starting URL to crawl. The URL is normalized and checked against "
                "the crawler security policy before fetching."
            ),
        },

        # ---------------------------------------------------------------------
        # Crawl behavior
        # ---------------------------------------------------------------------      
        "crawl_scope": {
            "type": "string",
            "required": False,
            "description": (
                "Scope of the crawl operation. "
                "Supported values: page, site, sitemap. "
                "page acquires only the requested URL. "
                "site discovers and traverses pages through links. "
                "sitemap resolves crawl targets from sitemap topology."
            ),
        },
        "sitemap_discovery_mode": {
        "type": "string",
        "required": False,
        "description": (
            "Controls sitemap discovery when crawl_scope=sitemap. "
            "planned discovers only as much sitemap content as needed "
            "to satisfy the bounded crawl plan. "
            "full resolves the complete sitemap tree and URL inventory. "
            "Defaults to planned. "
            "When sitemap_selection is provided, the effective mode "
            "becomes selected automatically."
        ),
    },
        "mode": {
            "type": "string",
            "required": False,
            "description": (
                "Fetch mode to use: static, browser, or auto. "
                "static performs a normal HTTP fetch. browser uses the browser fetcher "
                "when enabled by configuration. auto starts with static and may fall back "
                "to browser rendering when configured."
            ),
        },
        "auth_mode": {
            "type": "string",
            "required": False,
            "description": (
                "Authentication mode for protected sites. "
                "Supported values: none, delegated_entra, delegated_session, browser_context. "
                "Secrets are resolved server-side and never accepted directly from the user."
            ),
        },
        "browser_session_id": {
            "type": "string",
            "required": False,
            "description": (
                "Safe reference to a prepared browser session created by the browser-session "
                "domain. Used with auth_mode=delegated_session. This is not a cookie/token; "
                "the actual Playwright storage_state is resolved server-side."
            ),
        },
        "crawl_strategy": {
            "type": "string",
            "required": False,
            "description": (
                "Crawl traversal strategy: broad or deep. "
                "Aliases such as bfs/breadth_first and dfs/depth_first may be normalized "
                "by the tool."
            ),
        },
        
        "max_pages": {
            "type": "integer",
            "required": False,
            "description": (
                "Maximum number of pages to crawl for this run. "
                "The value is capped by the tool configuration."
            ),
        },
        "max_depth": {
            "type": "integer",
            "required": False,
            "description": (
                "Maximum link depth from the starting URL. "
                "0 means only the start page. The value is capped by the tool configuration."
            ),
        },
        "same_domain_only": {
            "type": "boolean",
            "required": False,
            "description": (
                "Whether to restrict crawling to the same domain as the starting URL. "
                "If omitted, the tool configuration default is used."
            ),
        },
        "include_links": {
            "type": "boolean",
            "required": False,
            "description": (
                "Whether discovered page links should be kept and followed during crawling. "
                "Set to false for a single-page evidence capture with no link expansion."
            ),
        },
        "max_chars_per_page": {
            "type": "integer",
            "required": False,
            "description": (
                "Maximum text characters to retain per crawled page. "
                "The value is capped/resolved by the tool configuration."
            ),
        },
        "timeout_seconds": {
            "type": "integer",
            "required": False,
            "description": (
                "Fetch timeout per page in seconds. "
                "If omitted, the configured timeout is used."
            ),
        },

        # ---------------------------------------------------------------------
        # Report/output behavior
        # ---------------------------------------------------------------------
        "report": {
            "type": "string",
            "required": False,
            "description": (
                "Comma-separated reports to include in result.reports. "
                "crawl_summary is always included. Use all to include every report. "
                "Available reports: seo, media, documents, links, forms, content_blocks, "
                "detected_html_blocks, snapshots, crawl_graph, find, filters, crawl_summary."
            ),
        },
        "output_format": {
            "type": "string",
            "required": False,
            "description": (
                "Preferred formatted output style. Supported values should be markdown or json. "
                "Currently intended for formatter-level behavior."
            ),
        },
        "include_raw_pages": {
            "type": "boolean",
            "required": False,
            "description": (
                "Whether to include the raw crawled page objects in the final payload. "
                "Set to false to keep workflow payloads smaller when only reports are needed."
            ),
        },
        "include_security_audit": {
            "type": "boolean",
            "required": False,
            "description": (
                "Whether to include detailed security audit entries in the final payload. "
                "crawl_summary can still include security counts even when detailed entries are omitted."
            ),
        },
        "include_errors": {
            "type": "boolean",
            "required": False,
            "description": (
                "Whether to include detailed crawl error messages in the final payload. "
                "crawl_summary can still include error counts even when detailed errors are omitted."
            ),
        },

        # ---------------------------------------------------------------------
        # Find/search behavior
        # ---------------------------------------------------------------------
        "find": {
            "type": "string",
            "required": False,
            "description": (
                "Optional text query to search for in the crawled page evidence and/or generated reports."
            ),
        },
        "find_scope": {
            "type": "string",
            "required": False,
            "description": (
                "Where to search for the find query. Suggested values: page, reports, or all."
            ),
        },
        "find_report": {
            "type": "string",
            "required": False,
            "description": (
                "Optional report area to search when find_scope includes reports. "
                "Examples: seo, links, forms, content_blocks, detected_html_blocks, "
                "documents, media, or all."
            ),
        },
        "case_sensitive": {
            "type": "boolean",
            "required": False,
            "description": (
                "Whether the find query should be case-sensitive. Defaults to false."
            ),
        },
        "max_matches": {
            "type": "integer",
            "required": False,
            "description": (
                "Maximum number of find matches to return."
            ),
        },

        # ---------------------------------------------------------------------
        # Media report filters
        # ---------------------------------------------------------------------
        "media_extensions": {
            "type": "array",
            "required": False,
            "items": {
                "type": "string",
            },
            "description": (
                "Filter media inventory by one or more extensions, e.g. "
                ".svg, .jpg, .jpeg, .png, .webp, .mp4. "
                "An item matches when its extension matches any selected value."
            ),
        },
        "media_filename": {
            "type": "string",
            "required": False,
            "description": (
                "Filter media inventory by filename or URL substring."
            ),
        },
        "media_role": {
            "type": "string",
            "required": False,
            "description": (
                "Filter media inventory by detected role, e.g. logo, icon, hero_candidate, "
                "product_candidate, background_image."
            ),
        },

        # ---------------------------------------------------------------------
        # Document report filters
        # ---------------------------------------------------------------------
        "document_extensions": {
            "type": "array",
            "required": False,
            "items": {
                "type": "string",
            },
            "description": (
                "Filter document inventory by one or more extensions, e.g. "
                ".pdf, .doc, .docx, .xls, .xlsx, .ppt, .pptx. "
                "An item matches when its extension matches any selected value."
            ),
        },
        "document_filename": {
            "type": "string",
            "required": False,
            "description": (
                "Filter document inventory by filename or URL substring."
            ),
        },
        "document_type": {
            "type": "string",
            "required": False,
            "description": (
                "Filter document inventory by normalized type, e.g. pdf, word, spreadsheet, presentation."
            ),
        },

        # ---------------------------------------------------------------------
        # Workflow/system context
        # ---------------------------------------------------------------------
        "context": {
            "type": "object",
            "required": False,
            "description": (
                "Optional workflow or agent context passed by the caller. "
                "Reserved for orchestration metadata and future crawler behavior."
            ),
        },
    }