from __future__ import annotations

from typing import Any

from src.domains.agentverse.tools.web_crawler_tool.config import WebCrawlerToolConfig
from src.domains.agentverse.tools.web_crawler_tool.constants import (
    DOCUMENT_EXTENSIONS,
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
)
from src.domains.agentverse.tools.web_crawler_tool.output_profiles import OUTPUT_PROFILES

WEB_CRAWLER_DEFAULTS = WebCrawlerToolConfig().model_dump()
WEB_CRAWLER_SCHEMA_FIELDS: dict[str, dict[str, Any]] = (
    WebCrawlerToolConfig.model_json_schema().get("properties", {})
)

WEB_CRAWLER_PROFILE_CONTROLLED_FIELDS = [
    "report",
    "output_format",
    "include_raw_pages",
    "include_security_audit",
    "include_errors",
    "max_pages",
    "max_depth",
    "include_links",
    "same_domain_only",
]

WEB_CRAWLER_REPORT_OPTIONS = [
    {"value": "crawl_summary", "label": "Crawl Summary"},
    {"value": "seo", "label": "SEO"},
    {"value": "links", "label": "Links"},
    {"value": "media", "label": "Media"},
    {"value": "documents", "label": "Documents"},
    {"value": "forms", "label": "Forms"},
    {"value": "content_blocks", "label": "Content Blocks"},
    {"value": "detected_html_blocks", "label": "Detected HTML Blocks"},
    {"value": "crawl_graph", "label": "Crawl Graph"},
    {"value": "find", "label": "Find"},
    {"value": "filters", "label": "Filters"},
    {"value": "metadata", "label": "Metadata"},
    {"value": "all", "label": "All"},
]

WEB_CRAWLER_SCOPE_OPTIONS = [
    {"value": "site", "label": "Site"},
    {"value": "sitemap", "label": "Sitemap"},
]

WEB_CRAWLER_OUTPUT_PROFILE_OPTIONS = [
    {"value": "general", "label": "General", "description": "Light, readable crawl for normal use."},
    {"value": "seo", "label": "SEO", "description": "SEO, links, content blocks and detected HTML blocks."},
    {"value": "workflow", "label": "Workflow", "description": "Compact structured output for workflow handoff."},
    {"value": "media", "label": "Media", "description": "Media inventory and filters."},
    {"value": "documents", "label": "Documents", "description": "Document inventory and filters."},
    {"value": "admin", "label": "Admin", "description": "Operational view with crawl graph and security details."},
    {"value": "full", "label": "Full", "description": "All reports without raw page payloads."},
    {"value": "debug", "label": "Debug", "description": "All reports with raw pages and diagnostics."},
    {
        "value": "media_light",
        "label": "Media (lightweight)",
        "description": "Fast media inventory with minimal page processing.",
    },
]

WEB_CRAWLER_MODE_OPTIONS = [
    {"value": "static", "label": "Static HTML"},
    {"value": "browser", "label": "Browser Rendered"},
    {"value": "auto", "label": "Auto"},
]
WEB_CRAWLER_STRATEGY_OPTIONS = [
    {"value": "broad", "label": "Broad"},
    {"value": "deep", "label": "Deep"},
]
WEB_CRAWLER_OUTPUT_FORMAT_OPTIONS = [
    {"value": "markdown", "label": "Markdown"},
    {"value": "json", "label": "JSON"},
]
WEB_CRAWLER_FIND_SCOPE_OPTIONS = [
    {"value": "all", "label": "All"},
    {"value": "page", "label": "Page"},
    {"value": "reports", "label": "Reports"},
]
WEB_CRAWLER_FIND_REPORT_OPTIONS = [
    {"value": "all", "label": "All reports"},
    {"value": "seo", "label": "SEO"},
    {"value": "links", "label": "Links"},
    {"value": "forms", "label": "Forms"},
    {"value": "content_blocks", "label": "Content Blocks"},
    {"value": "detected_html_blocks", "label": "Detected HTML Blocks"},
    {"value": "documents", "label": "Documents"},
    {"value": "media", "label": "Media"},
]
WEB_CRAWLER_MEDIA_ROLE_OPTIONS = [
    {"value": "logo", "label": "Logo"},
    {"value": "icon", "label": "Icon"},
    {"value": "hero_candidate", "label": "Hero Candidate"},
    {"value": "product_candidate", "label": "Product Candidate"},
    {"value": "content", "label": "Content"},
    {"value": "decorative", "label": "Decorative"},
    {"value": "unknown", "label": "Unknown"},
]
WEB_CRAWLER_DOCUMENT_TYPE_OPTIONS = [
    {"value": "pdf", "label": "PDF"},
    {"value": "word", "label": "Word"},
    {"value": "spreadsheet", "label": "Spreadsheet"},
    {"value": "presentation", "label": "Presentation"},
    {"value": "text", "label": "Text"},
    {"value": "data", "label": "Data"},
]
WEB_CRAWLER_MEDIA_EXTENSIONS_OPTIONS = [
    {"value": extension, "label": extension}
    for extension in sorted(IMAGE_EXTENSIONS | VIDEO_EXTENSIONS)
]
WEB_CRAWLER_DOCUMENT_EXTENSIONS_OPTIONS = [
    {"value": extension, "label": extension}
    for extension in sorted(DOCUMENT_EXTENSIONS.keys())
]
WEB_CRAWLER_SITEMAP_DISCOVERY_MODE_OPTIONS = [
    {
        "value": "planned",
        "label": "Planned",
        "description": (
            "Discover only as much sitemap content as needed "
            "to build the bounded crawl plan."
        ),
    },
    {
        "value": "full",
        "label": "Full",
        "description": (
            "Resolve the complete sitemap tree and URL inventory."
        ),
    },
]

WEB_CRAWLER_SITEMAP_TRAVERSAL_POLICY_OPTIONS = [
    {
        "value": "bounded",
        "label": "Bounded",
        "description": (
            "Stop sitemap traversal at the configured sitemap limit."
        ),
    },
    {
        "value": "complete",
        "label": "Complete",
        "description": (
            "Resolve the complete sitemap topology before planning."
        ),
    },
]


def _config_props(
    field_name: str,
    *,
    label: str,
    options: list[dict[str, Any]] | None = None,
    helper_text: str | None = None,
    placeholder: str | None = None,
    step: int | float | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    schema = WEB_CRAWLER_SCHEMA_FIELDS.get(field_name, {})
    props: dict[str, Any] = {
        "label": label,
        "defaultValue": WEB_CRAWLER_DEFAULTS.get(field_name),
    }
    if "minimum" in schema:
        props["min"] = schema["minimum"]
    if "maximum" in schema:
        props["max"] = schema["maximum"]
    if options is not None:
        props["options"] = options
    if helper_text is not None:
        props["helperText"] = helper_text
    if placeholder is not None:
        props["placeholder"] = placeholder
    if step is not None:
        props["step"] = step
    if extra:
        props.update(extra)
    return props


def _component(
    *,
    id: str,
    type: str,
    widget: str,
    group: str | None,
    flag: str,
    render_paths: list[str],
    slot_order: int,
    props: dict[str, Any],
    required: bool = False,
    profile_binding: dict[str, Any] | None = None,
    visible_when: dict[str, Any] | None = None,
    disabled_when: list[dict[str, Any]] | None = None,
    actions: list[dict[str, Any]] | None = None,
    hidden: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": id,
        "type": type,
        "widget": widget,
        "group": group,
        "flag_config": {"flag": flag, "required": required},
        "render_paths": render_paths,
        "slot_order": slot_order,
        "props": props,
    }
    if profile_binding:
        payload["profile_binding"] = profile_binding
    if visible_when:
        payload["visible_when"] = visible_when
    if disabled_when:
        payload["disabled_when"] = disabled_when
    if actions:
        payload["actions"] = actions
    if hidden:
        payload["hidden"] = True
    return payload


def build_output_profile_metadata() -> dict[str, Any]:
    return {
        profile_name: {
            "defaults": profile,
            "preview": {
                "reports": str(profile.get("report", "")).split(","),
                "output_format": profile.get("output_format"),
                "max_pages": profile.get("max_pages"),
                "max_depth": profile.get("max_depth"),
                "include_raw_pages": profile.get("include_raw_pages"),
                "include_security_audit": profile.get("include_security_audit"),
            },
        }
        for profile_name, profile in OUTPUT_PROFILES.items()
    }

WEB_CRAWLER_UI_METADATA: dict[str, Any] = {
    "icon": "🕷️",
    "category": "web",
    "display_hints": [
        "crawler",
        "bounded",
        "llm-free",
        "site-discovery",
        "evidence",
    ],
    "config": {
        "chat_input": {
            "id": "url_input",
            "placeholder": "Enter a website URL to crawl...",
            "file_upload": False,
            "flag": "--url",
            "required": True,
        },

        "group_label_map": {
            "Setup": "Target & crawl",
            "Output": "Output",
            "Search": "Search",
            "Filters": "Filters",
            "Advanced": "Advanced",
        },

        "output_profiles": {
            "default": "media_light",
            "controlled_fields": WEB_CRAWLER_PROFILE_CONTROLLED_FIELDS,
            "profiles": build_output_profile_metadata(),
            "options": WEB_CRAWLER_OUTPUT_PROFILE_OPTIONS,
            "apply_values": True,
            "respect_user_overrides": True,
        },

        # -------------------------------------------------------------
        # Operation binding.
        #
        # The client can use this contract to bind generic operation
        # controls to the backend/domain-event lifecycle rather than
        # implementing crawler-specific start/stop state.
        # -------------------------------------------------------------
       "operation_binding": {
            "source": "web_crawler",
            "kind": "crawl",

            "run_id_state": "operation.run_id",
            "status_state": "operation.status",

            "running_statuses": [
                "created",
                "running",
            ],

            "stopping_statuses": [
                "cancelling",
            ],

            "terminal_statuses": [
                "completed",
                "failed",
                "cancelled",
            ],

            "events": {
                "started": "operation.started",
                "progress": "operation.progress",
                "completed": "operation.completed",
                "failed": "operation.failed",
                "cancelled": "operation.cancelled",
            },

            "actions": {
                "cancel": {
                    "type": "http_request",
                    "method": "POST",
                    "endpoint": (
                        "/v1/operations/"
                        "{run_id}/cancel"
                    ),
                    "path_params": {
                        "run_id": {
                            "from_state": (
                                "operation.run_id"
                            ),
                        },
                    },
                },
            },
        },

        "feature_panels": [
            {
                "id": "search",
                "label": "Search within crawl",
                "description": (
                    "Find text, assets, documents, or evidence."
                ),
                "produces_report": "find",
                "fields": [
                    "find",
                    "find_scope",
                    "find_report",
                    "case_sensitive",
                    "max_matches",
                ],
            },
            {
                "id": "filters",
                "label": "Filters",
                "description": (
                    "Narrow media and document inventory results."
                ),
                "produces_report": "filters",
                "groups": [
                    {
                        "id": "media",
                        "label": "Media",
                        "fields": [
                            "media_extensions",
                            "media_filename",
                            "media_role",
                        ],
                    },
                    {
                        "id": "documents",
                        "label": "Documents",
                        "fields": [
                            "document_extensions",
                            "document_filename",
                            "document_type",
                        ],
                    },
                ],
            },
            {
                "id": "advanced",
                "label": "Advanced settings",
                "description": (
                    "Fine-tune crawl behavior, output, security and runtime limits."
                ),
                "groups": [
                    {
                        "id": "crawl",
                        "label": "Crawl",
                        "fields": [
                            "mode",
                            "crawl_strategy",
                            "max_pages",
                            "max_depth",
                            "include_links",
                            "same_domain_only",
                        ],
                    },
                    {
                        "id": "output",
                        "label": "Output",
                        "fields": [
                            "report",
                            "output_format",
                            "include_raw_pages",
                            "include_errors",
                        ],
                    },
                    {
                        "id": "security",
                        "label": "Security",
                        "fields": [
                            "include_security_audit",
                        ],
                    },
                    {
                        "id": "runtime",
                        "label": "Runtime limits",
                        "fields": [
                            "timeout_seconds",
                            "max_chars_per_page",
                        ],
                    },
                ],
            },
        ],

        "search_behavior": {
            "field": "find",
            "produces_report": "find",
            "inference": [
                {
                    "match": "option_value",
                    "source_options": "media_extensions",
                    "target_field": "media_extensions",
                    "target_mode": "append",
                    "also_include_report": "filters",
                },
                {
                    "match": "option_value",
                    "source_options": "document_extensions",
                    "target_field": "document_extensions",
                    "also_include_report": "filters",
                },
            ],
        },
    },

    "execution_mapping": {
        "auth_mode": {
            "if": {
                "and": [
                    {
                        "state": "browser_session.ready",
                        "equals": True,
                    },
                    {
                        "state": "browser_session.domain",
                        "matches_domain_of": "$form.url",
                    },
                ]
            },
            "then": "delegated_session",
            "else": "none",
        },
        "browser_session_id": {
            "from_state": "browser_session.browser_session_id",
            "when": {
                "and": [
                    {
                        "state": "browser_session.ready",
                        "equals": True,
                    },
                    {
                        "state": "browser_session.domain",
                        "matches_domain_of": "$form.url",
                    },
                ]
            },
        },
    },

    "components": [
        _component(
            id="url",
            type="text",
            widget="TextField",
            group="Setup",
            flag="--url",
            required=True,
            render_paths=[
                "propertiesPanel.setup",
                "workspace.conversationArea.inputSection.main",
            ],
            slot_order=0,
            props={
                "label": "Target URLs",
                "placeholder": "https://www.example.com",
                "helperText": (
                    "The crawler starts from this URL. "
                    "Use Sign in if the page requires intranet or SSO access."
                ),
            },
        ),

        _component(
            id="crawl_scope",
            type="select",
            widget="SelectField",
            group="Setup",
            flag="--crawl_scope",
            render_paths=[
                "propertiesPanel.setup",
            ],
            slot_order=1,
            props=_config_props(
                "crawl_scope",
                label="Crawl Scope",
                options=WEB_CRAWLER_SCOPE_OPTIONS,
                helper_text=(
                    "Choose whether to crawl a single page, "
                    "discover a site through links, or use sitemap topology."
                ),
            ),
        ),
        
        _component(
            id="sitemap_discovery_mode",
            type="select",
            widget="SelectField",
            group="Setup",
            flag="--sitemap_discovery_mode",
            render_paths=[
                "propertiesPanel.setup",
            ],
            slot_order=2,
            visible_when={
                "field": "crawl_scope",
                "operator": "in",
                "value": [
                    "sitemap",
                ],
            },
            props=_config_props(
                "sitemap_discovery_mode",
                label="Sitemap Discovery",
                options=(
                    WEB_CRAWLER_SITEMAP_DISCOVERY_MODE_OPTIONS
                ),
                helper_text=(
                    "Planned discovers only enough sitemap content "
                    "to satisfy the crawl plan. Full resolves the "
                    "complete sitemap tree."
                ),
            ),
        ),

        _component(
            id="sitemap_traversal_policy",
            type="select",
            widget="SelectField",
            group="Setup",
            flag="--sitemap_traversal_policy",
            render_paths=["propertiesPanel.setup"],
            slot_order=3,
            visible_when={
                "field": "crawl_scope",
                "operator": "in",
                "value": ["sitemap"],
            },
            props=_config_props(
                "sitemap_traversal_policy",
                label="Sitemap Traversal",
                options=WEB_CRAWLER_SITEMAP_TRAVERSAL_POLICY_OPTIONS,
                helper_text=(
                    "Bounded limits sitemap discovery. Complete resolves "
                    "the full sitemap tree."
                ),
            ),
        ),

        _component(
            id="max_sitemaps",
            type="number",
            widget="NumberField",
            group="Setup",
            flag="--max_sitemaps",
            render_paths=["propertiesPanel.setup"],
            slot_order=4,
            visible_when={
                "field": "crawl_scope",
                "operator": "in",
                "value": ["sitemap"],
            },
            props={
                "label": "Max Sitemaps",
                "defaultValue": 1,
                "min": 1,
                "step": 1,
                "helperText": (
                    "Maximum number of sitemap resources to resolve. "
                    "This limits discovery breadth, not page crawling."
                ),
            },
        ),

        _component(
            id="page_acquisition_limit",
            type="number",
            widget="NumberField",
            group="Setup",
            flag="--page_acquisition_limit",
            render_paths=["propertiesPanel.setup"],
            slot_order=5,
            visible_when={
                "field": "crawl_scope",
                "operator": "in",
                "value": ["sitemap"],
            },
            props={
                "label": "Page Acquisition Limit",
                "defaultValue": 1000,
                "min": 1,
                "step": 1,
                "helperText": (
                    "Maximum number of sitemap pages to acquire."
                ),
            },
        ),

        _component(
            id="start_browser_session",
            type="action",
            widget="ActionButton",
            group="Setup",
            flag="",
            render_paths=[
                "propertiesPanel.setup",
                "workspace.conversationArea.inputSection.actions",
            ],
            slot_order=90,
            disabled_when=[
                {
                    "field": "url",
                    "operator": "empty",
                    "message": (
                        "Enter a target URL before signing in."
                    ),
                },
                {
                    "state": "operation.status",
                    "operator": "in",
                    "value": [
                        "created",
                        "running",
                        "cancelling",
                    ],
                    "message": (
                        "Wait until the current crawl has stopped."
                    ),
                },
            ],
            props={
                "label": "Sign in",
                "description": (
                    "Use this if the target page requires "
                    "intranet or SSO access."
                ),
                "helperText": (
                    "Starts a temporary browser session and opens "
                    "the interactive sign-in page."
                ),
                "variant": "secondary",
                "action": {
                    "type": "http_request",
                    "endpoint": "/api/v1/browser-sessions/start",
                    "method": "POST",
                    "body": {
                        "target_url": {
                            "from_field": "url",
                            "transform": "trim",
                        },
                        "ttl_seconds": {
                            "value": 1800,
                        },
                        "headed": {
                            "value": True,
                        },
                        "success_url_contains": {
                            "from_field": "url",
                            "transform": "hostname",
                        },
                    },
                    "response_mapping": {
                        "browser_session_id": {
                            "from_response": (
                                "data.browser_session_id"
                            ),
                            "to_field": "browser_session_id",
                        },
                    },
                    "open_url": {
                        "from_response": "data.interactive_url",
                        "target": "_blank",
                        "open_before_request": False,
                    },
                    "poll": {
                        "endpoint": (
                            "/api/v1/browser-sessions/status"
                        ),
                        "method": "POST",
                        "interval_ms": 2000,
                        "timeout_ms": 300000,
                        "body": {
                            "browser_session_id": {
                                "from_response": (
                                    "data.browser_session_id"
                                ),
                            },
                        },
                        "until": {
                            "field": "status",
                            "equals": "authenticated",
                        },
                        "failure_states": [
                            "failed",
                            "expired",
                            "deleted",
                        ],
                        "response_mapping": {
                            "browser_session_id": {
                                "from_response": (
                                    "data.browser_session_id"
                                ),
                                "to_field": "browser_session_id",
                            },
                            "auth_mode": {
                                "value": "delegated_session",
                                "to_field": "auth_mode",
                            },
                            "browser_session.browser_session_id": {
                                "from_response": (
                                    "data.browser_session_id"
                                ),
                                "to_state": (
                                    "browser_session.browser_session_id"
                                ),
                            },
                            "browser_session.ready": {
                                "from_response": "data.ready",
                                "to_state": "browser_session.ready",
                            },
                            "browser_session.domain": {
                                "from_response": "data.domain",
                                "to_state": "browser_session.domain",
                            },
                            "browser_session.status": {
                                "from_response": "data.status",
                                "to_state": "browser_session.status",
                            },
                        },
                    },
                    "labels": {
                        "idle": "Sign in",
                        "loading": "Starting browser...",
                        "waiting": "Waiting for sign-in...",
                        "success": "Signed in",
                        "error": "Sign-in failed",
                    },
                },
            },
        ),

        # -------------------------------------------------------------
        # Crawl operation controls.
        #
        # These are semantic backend-driven actions. A generic client
        # action renderer can execute the tool, bind the resulting
        # operation lifecycle, and cancel by run_id without containing
        # crawler-specific business logic.
        # -------------------------------------------------------------
        _component(
            id="start_crawl",
            type="action",
            widget="ActionButton",
            group="Setup",
            flag="",
            render_paths=[
                "propertiesPanel.setup",
                "workspace.conversationArea.inputSection.actions",
            ],
            slot_order=91,
            visible_when={
                "state": "operation.status",
                "operator": "not_in",
                "value": [
                    "created",
                    "running",
                    "cancelling",
                ],
            },
            disabled_when=[
                {
                    "field": "url",
                    "operator": "empty",
                    "message": (
                        "Enter a target URL before crawling."
                    ),
                },
            ],
            props={
                "label": "Crawl",
                "description": (
                    "Start the crawler using the current form values."
                ),
                "variant": "primary",
                "action": {
                    "type": "tool_execute",
                    "tool_key": "web_crawler",
                    "input": {
                        "source": "form",
                        "include": "flagged_fields",
                    },
                    "operation_binding": (
                        "ui.config.operation_binding"
                    ),
                    "labels": {
                        "idle": "Crawl",
                        "loading": "Starting...",
                        "success": "Crawl started",
                        "error": "Could not start crawl",
                    },
                },
            },
        ),

        _component(
            id="stop_crawl",
            type="action",
            widget="ActionButton",
            group="Setup",
            flag="",
            render_paths=[
                "propertiesPanel.setup",
                "workspace.conversationArea.inputSection.actions",
            ],
            slot_order=92,
            visible_when={
                "state": "operation.status",
                "operator": "in",
                "value": [
                    "created",
                    "running",
                    "cancelling",
                ],
            },
            disabled_when=[
                {
                    "state": "operation.status",
                    "operator": "in",
                    "value": [
                        "cancelling",
                    ],
                    "message": (
                        "The crawl is already stopping."
                    ),
                },
            ],
            props={
                "label": "Stop",
                "description": (
                    "Cancel the currently running crawl operation."
                ),
                "variant": "secondary",
                "action": {
                    "type": "operation_cancel",
                    "run_id": {
                        "from_state": "operation.run_id",
                    },
                    "operation_binding": (
                        "ui.config.operation_binding"
                    ),
                    "labels": {
                        "idle": "Stop",
                        "loading": "Stopping...",
                        "success": "Crawl stopped",
                        "error": "Could not stop crawl",
                    },
                },
            },
        ),

        _component(
            id="auth_mode",
            type="string",
            widget="HiddenField",
            group=None,
            flag="--auth_mode",
            render_paths=[],
            slot_order=100,
            hidden=True,
            props={
                "label": "Authentication Mode",
                "defaultValue": "none",
            },
        ),

        _component(
            id="browser_session_id",
            type="string",
            widget="HiddenField",
            group=None,
            flag="--browser_session_id",
            render_paths=[],
            slot_order=101,
            hidden=True,
            props={
                "label": "Browser Session ID",
            },
        ),

        _component(
            id="output_profile",
            type="select",
            widget="SelectField",
            group="Setup",
            flag="--output_profile",
            render_paths=[
                "propertiesPanel.setup",
                "workspace.conversationArea.inputSection.toolMode",
            ],
            slot_order=3,
            profile_binding={
                "source": "ui.config.output_profiles",
                "apply_values": True,
                "respect_user_overrides": True,
            },
            props={
                "label": "Output Profile",
                "defaultValue": "general",
                "options": WEB_CRAWLER_OUTPUT_PROFILE_OPTIONS,
                "helperText": (
                    "Choose the crawler output preset."
                ),
            },
        ),

        _component(
            id="crawl_strategy",
            type="select",
            widget="SelectField",
            group="Setup",
            flag="--crawl_strategy",
            render_paths=["propertiesPanel.setup"],
            slot_order=4,
            visible_when={
                "field": "crawl_scope",
                "operator": "in",
                "value": [
                    "site",
                    "sitemap",
                ],
            },
            props=_config_props(
                "crawl_strategy",
                label="Crawl Strategy",
                options=WEB_CRAWLER_STRATEGY_OPTIONS,
                helper_text=(
                    "Broad explores sibling links first. "
                    "Deep follows branches first."
                ),
            ),
        ),

        _component(
            id="max_pages",
            type="number",
            widget="NumberField",
            group="Setup",
            flag="--max_pages",
            render_paths=["propertiesPanel.setup"],
            slot_order=5,
            visible_when={
                "field": "crawl_scope",
                "operator": "in",
                "value": [
                    "site",
                    "sitemap",
                ],
            },
            props=_config_props(
                "max_pages",
                label="Max Pages",
                step=1,
                helper_text="Maximum number of pages to crawl.",
            ),
        ),

        _component(
            id="max_depth",
            type="number",
            widget="NumberField",
            group="Setup",
            flag="--max_depth",
            render_paths=["propertiesPanel.setup"],
            slot_order=6,
            visible_when={
                "field": "crawl_scope",
                "operator": "in",
                "value": [
                    "site",
                ],
            },
            props=_config_props(
                "max_depth",
                label="Max Depth",
                step=1,
                helper_text="0 means only the start page.",
            ),
        ),

        _component(
            id="include_links",
            type="boolean",
            widget="SwitchField",
            group="Setup",
            flag="--include_links",
            render_paths=["propertiesPanel.setup"],
            slot_order=7,
            visible_when={
                "field": "crawl_scope",
                "operator": "in",
                "value": [
                    "site",
                ],
            },
            props={
                "label": "Follow Links",
                "helperText": (
                    "Disable for a single-page evidence capture."
                ),
            },
        ),

        _component(
            id="report",
            type="multiselect",
            widget="CheckboxGroupField",
            group="Output",
            flag="--report",
            render_paths=["propertiesPanel.output"],
            slot_order=0,
            props={
                "label": "Reports",
                "options": WEB_CRAWLER_REPORT_OPTIONS,
                "helperText": (
                    "Select which structured reports to include."
                ),
            },
        ),

        _component(
            id="output_format",
            type="select",
            widget="SelectField",
            group="Output",
            flag="--output_format",
            render_paths=["propertiesPanel.output"],
            slot_order=1,
            props={
                "label": "Output Format",
                "options": WEB_CRAWLER_OUTPUT_FORMAT_OPTIONS,
            },
        ),

        _component(
            id="include_raw_pages",
            type="boolean",
            widget="SwitchField",
            group="Output",
            flag="--include_raw_pages",
            render_paths=["propertiesPanel.output"],
            slot_order=2,
            props={
                "label": "Include Raw Pages",
                "helperText": "Raw pages can be large.",
            },
        ),

        _component(
            id="include_errors",
            type="boolean",
            widget="SwitchField",
            group="Output",
            flag="--include_errors",
            render_paths=["propertiesPanel.output"],
            slot_order=3,
            props={
                "label": "Include Error Details",
            },
        ),

        # -------------------------------------------------------------
        # Search feature
        # -------------------------------------------------------------
        _component(
            id="find",
            type="text",
            widget="TextField",
            group="Search",
            flag="--find",
            render_paths=["propertiesPanel.search"],
            slot_order=0,
            props={
                "label": "Find Text",
                "placeholder": (
                    "e.g. sustainability, .svg"
                ),
            },
        ),

        _component(
            id="find_scope",
            type="select",
            widget="SelectField",
            group="Search",
            flag="--find_scope",
            render_paths=["propertiesPanel.search"],
            slot_order=1,
            props={
                "label": "Find Scope",
                "options": WEB_CRAWLER_FIND_SCOPE_OPTIONS,
            },
        ),

        _component(
            id="find_report",
            type="select",
            widget="SelectField",
            group="Search",
            flag="--find_report",
            render_paths=["propertiesPanel.search"],
            slot_order=2,
            visible_when={
                "field": "find_scope",
                "operator": "in",
                "value": ["reports", "all"],
            },
            props={
                "label": "Find Report",
                "options": WEB_CRAWLER_FIND_REPORT_OPTIONS,
            },
        ),

        _component(
            id="case_sensitive",
            type="boolean",
            widget="SwitchField",
            group="Search",
            flag="--case_sensitive",
            render_paths=["propertiesPanel.search"],
            slot_order=3,
            props={
                "label": "Case Sensitive",
            },
        ),

        _component(
            id="max_matches",
            type="number",
            widget="NumberField",
            group="Search",
            flag="--max_matches",
            render_paths=["propertiesPanel.search"],
            slot_order=4,
            props=_config_props(
                "max_matches",
                label="Max Matches",
                step=1,
                helper_text=(
                    "Maximum number of matching results to retain."
                ),
            ),
        ),

        # -------------------------------------------------------------
        # Filters feature
        # -------------------------------------------------------------
        _component(
            id="media_extensions",
            type="multiselect",
            widget="CheckboxGroupField",
            group="Filters",
            flag="--media_extensions",
            render_paths=["propertiesPanel.filters"],
            slot_order=0,
            props={
                "label": "Media extensions",
                "defaultValue": [],
                "options": WEB_CRAWLER_MEDIA_EXTENSIONS_OPTIONS,
                "helperText": (
                    "Include media matching any selected extension."
                ),
            },
        ),

        _component(
            id="media_filename",
            type="text",
            widget="TextField",
            group="Filters",
            flag="--media_filename",
            render_paths=["propertiesPanel.filters"],
            slot_order=1,
            props={
                "label": "Media Filename",
                "placeholder": "logo, hero, product",
            },
        ),

        _component(
            id="media_role",
            type="select",
            widget="SelectField",
            group="Filters",
            flag="--media_role",
            render_paths=["propertiesPanel.filters"],
            slot_order=2,
            props={
                "label": "Media Role",
                "options": WEB_CRAWLER_MEDIA_ROLE_OPTIONS,
            },
        ),

        _component(
            id="document_extensions",
            type="multiselect",
            widget="CheckboxGroupField",
            group="Filters",
            flag="--document_extensions",
            render_paths=["propertiesPanel.filters"],
            slot_order=3,
            props={
                "label": "Document extensions",
                "defaultValue": [],
                "options": WEB_CRAWLER_DOCUMENT_EXTENSIONS_OPTIONS,
                "helperText": (
                    "Include documents matching any selected extension."
                ),
            },
        ),

        _component(
            id="document_filename",
            type="text",
            widget="TextField",
            group="Filters",
            flag="--document_filename",
            render_paths=["propertiesPanel.filters"],
            slot_order=4,
            props={
                "label": "Document Filename",
                "placeholder": "report, strategy, policy",
            },
        ),

        _component(
            id="document_type",
            type="select",
            widget="SelectField",
            group="Filters",
            flag="--document_type",
            render_paths=["propertiesPanel.filters"],
            slot_order=5,
            props={
                "label": "Document Type",
                "options": WEB_CRAWLER_DOCUMENT_TYPE_OPTIONS,
            },
        ),

        # -------------------------------------------------------------
        # Advanced feature
        # -------------------------------------------------------------
        _component(
            id="same_domain_only",
            type="boolean",
            widget="SwitchField",
            group="Advanced",
            flag="--same_domain_only",
            render_paths=["propertiesPanel.advanced"],
            slot_order=0,
            props=_config_props(
                "same_domain_only",
                label="Same Domain Only",
                helper_text=(
                    "Restrict crawling to the start URL domain."
                ),
            ),
        ),

        _component(
            id="include_security_audit",
            type="boolean",
            widget="SwitchField",
            group="Advanced",
            flag="--include_security_audit",
            render_paths=["propertiesPanel.advanced"],
            slot_order=1,
            props={
                "label": "Include Security Audit",
            },
        ),

        _component(
            id="timeout_seconds",
            type="number",
            widget="NumberField",
            group="Advanced",
            flag="--timeout_seconds",
            render_paths=["propertiesPanel.advanced"],
            slot_order=2,
            props=_config_props(
                "timeout_seconds",
                label="Timeout Seconds",
                step=1,
                helper_text=(
                    "Maximum time allowed for each page request."
                ),
            ),
        ),

        _component(
            id="max_chars_per_page",
            type="number",
            widget="NumberField",
            group="Advanced",
            flag="--max_chars_per_page",
            render_paths=["propertiesPanel.advanced"],
            slot_order=3,
            props=_config_props(
                "max_chars_per_page",
                label="Max Chars Per Page",
                step=500,
                helper_text=(
                    "Maximum text characters retained per crawled page."
                ),
            ),
        ),
    ],

    "examples": [
        {
            "id": "general_scan",
            "label": "General site scan",
            "description": "Light crawl for normal use.",
            "params": {
                "url": "https://www.example.com",
                "output_profile": "general",
            },
        },
        {
            "id": "seo_scan",
            "label": "SEO scan",
            "description": "SEO-focused crawl.",
            "params": {
                "url": "https://www.example.com",
                "output_profile": "seo",
                "max_pages": 5,
                "max_depth": 1,
            },
        },
        {
            "id": "find_phrase",
            "label": "Find a phrase",
            "description": "Search across crawl evidence.",
            "params": {
                "url": "https://www.example.com",
                "output_profile": "debug",
                "max_pages": 3,
                "find": "sustainability",
            },
        },
        {
            "id": "media_inventory",
            "label": "Find image media",
            "description": "Inventory common image assets.",
            "params": {
                "url": "https://www.example.com",
                "output_profile": "media",
                "media_extensions": [
                    ".jpg",
                    ".jpeg",
                    ".png",
                    ".webp",
                ],
            },
        },
        {
            "id": "document_inventory",
            "label": "Find documents",
            "description": "Inventory common document formats.",
            "params": {
                "url": "https://www.example.com",
                "output_profile": "documents",
                "document_extensions": [
                    ".pdf",
                    ".docx",
                    ".xlsx",
                    ".pptx",
                ],
            },
        },
    ],
}
