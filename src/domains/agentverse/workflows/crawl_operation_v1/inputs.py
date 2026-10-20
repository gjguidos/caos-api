from __future__ import annotations

from typing import Any

from src.domains.agentverse.workflows.spec import WorkflowInputField


def workflow_option(
    value: Any,
    label: str | None = None,
) -> dict[str, Any]:
    return {
        "label": label or str(value),
        "value": value,
    }


def workflow_inputs() -> list[WorkflowInputField]:
    return [
        WorkflowInputField(
            key="url",
            label="URL",
            input="text",
            required=True,
            help="Target URL to crawl.",
        ),
        WorkflowInputField(
            key="mode",
            label="Mode",
            input="select",
            default="auto",
            options=[
                workflow_option("auto", "Auto"),
                workflow_option("static", "Static"),
                workflow_option("browser", "Browser"),
            ],
            help="Crawler fetch mode.",
        ),
        WorkflowInputField(
            key="auth_mode",
            label="Authentication mode",
            input="select",
            default="none",
            options=[
                workflow_option("none", "None"),
                workflow_option("delegated_entra", "Delegated Entra"),
                workflow_option("delegated_session", "Delegated session"),
                workflow_option("browser_context", "Browser context"),
            ],
            help="Authentication strategy used while crawling.",
        ),
        WorkflowInputField(
            key="browser_session_id",
            label="Browser session ID",
            input="text",
            help="Existing authenticated browser session ID.",
        ),
        WorkflowInputField(
            key="output_profile",
            label="Output profile",
            input="select",
            default="general",
            options=[
                workflow_option("general", "General"),
                workflow_option("workflow", "Workflow"),
                workflow_option("admin", "Admin"),
                workflow_option("debug", "Debug"),
            ],
            help="Preconfigured crawler output profile.",
        ),
        WorkflowInputField(
            key="output_format",
            label="Output format",
            input="select",
            default="markdown",
            options=[
                workflow_option("markdown", "Markdown"),
                workflow_option("json", "JSON"),
            ],
            help="Format used for the crawler result.",
        ),
        WorkflowInputField(
            key="crawl_strategy",
            label="Crawl strategy",
            input="select",
            default="broad",
            options=[
                workflow_option("broad", "Broad"),
                workflow_option("deep", "Deep"),
            ],
            help="Strategy used to discover pages.",
        ),
        WorkflowInputField(
            key="max_pages",
            label="Max pages",
            input="number",
            default=5,
            help="Maximum number of pages to crawl.",
        ),
        WorkflowInputField(
            key="max_depth",
            label="Max depth",
            input="number",
            default=1,
            help="Maximum crawl depth from the starting URL.",
        ),
        WorkflowInputField(
            key="same_domain_only",
            label="Same domain only",
            input="checkbox",
            default=True,
            help="Restrict crawling to the starting domain.",
        ),
        WorkflowInputField(
            key="include_links",
            label="Include links",
            input="checkbox",
            default=True,
            help="Include discovered links in the crawler output.",
        ),
        WorkflowInputField(
            key="include_raw_pages",
            label="Include raw pages",
            input="checkbox",
            default=False,
            help="Include raw crawled page data in the result.",
        ),
        WorkflowInputField(
            key="include_security_audit",
            label="Include security audit",
            input="checkbox",
            default=False,
            help="Include security-related crawler analysis.",
        ),
        WorkflowInputField(
            key="include_errors",
            label="Include errors",
            input="checkbox",
            default=True,
            help="Include crawl errors in the result.",
        ),
        WorkflowInputField(
            key="report",
            label="Reports",
            input="text",
            default="crawl_summary,links,documents,media,seo",
            help="Comma-separated reports to generate.",
        ),
        WorkflowInputField(
            key="find",
            label="Find",
            input="text",
            help="Optional text to search for in crawled content.",
        ),
        WorkflowInputField(
            key="case_sensitive",
            label="Case sensitive",
            input="checkbox",
            default=False,
            help="Use case-sensitive matching for Find.",
        ),
        WorkflowInputField(
            key="max_matches",
            label="Max matches",
            input="number",
            default=20,
            help="Maximum number of Find matches to return.",
        ),
    ]