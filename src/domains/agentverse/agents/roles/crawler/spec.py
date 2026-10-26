# These should eventually be crawler/form-agent specific handlers.
# For the first pass they can be generic operational handlers.
#
from src.domains.agentverse.agents.roles.crawler.handlers.h_analyze import h_analyze
from src.domains.agentverse.agents.roles.crawler.handlers.h_evaluate import h_evaluate
from src.domains.agentverse.agents.roles.crawler.handlers.h_plan import h_plan
from src.domains.agentverse.agents.roles.crawler.handlers.h_refine import h_refine
from src.domains.agentverse.agents.roles.crawler.handlers.h_task import h_task
from src.domains.agentverse.agents.roles.role_spec import (
    PhaseSpec,
    RolePolicy,
    RoleSpec,
)

role_spec = RoleSpec(
    ownership="global",
    key="crawler",
    name="Crawler",
    version="1.0.0",
    policy=RolePolicy(
        name="crawler",
        temperature=0.1,
        samples=1,
        quality_threshold=0.85,
    ),
    handlers={
        # Keep these generic until crawler-specific handlers exist.
        #
        "analyze": h_analyze,
        "plan": h_plan,
        "task": h_task,
        "refine": h_refine,
        "evaluate": h_evaluate,
    },
    agent_group="interactive_ui",
    agent_subclass="operational",
    default_tools=[
        "web_crawler",
        "workflow",
    ],
    capabilities={
        "routing": "operation-first",
        "chat_fallback": False,
        "refine_loop": "disabled",
        "interface": {
            "type": "form_agent",
            "layout": "tool_form",
            "role_adapter": "crawler",
        },
        "operations": {
            "default": "crawl",
            "items": {
                "crawl": {
                    "label": "Start crawl",
                    "description": (
                        "Crawl a target, normalize crawl reports, store the output, "
                        "and return stored report references."
                    ),
                    "form_source": {
                        "type": "tool",
                        "key": "web_crawler",
                    },
                    "runner": {
                        "type": "workflow",
                        "key": "crawl_operation_v1",
                    },
                    "result_contract": "crawler.operation_result.v1",
                },
            },
        },
        "form_source": {
            "type": "tool",
            "key": "web_crawler",
        },
        "submit_target": {
            "type": "workflow",
            "key": "crawl_operation_v1",
        },
        "required_tools": {
            "web_crawler": "Web Crawler Tool",
            "workflow": "Workflow Tool",
        },
        "required_workflows": {
            "crawl": "crawl_operation_v1",
        },
        "optional_tools": {
            "normalizer": "Crawl Report Normalizer Tool",
            "store": "Output Store Tool",
            "artifact_builder": "Artifact Builder Tool",
        },
        "runtime_events": {
            "enabled": True,
            "bridge_events": [
                "browser_session.authenticated",
                "browser_session.failed",
                "tool.started",
                "tool.completed",
                "tool.failed",
                "workflow.started",
                "workflow.step.started",
                "workflow.step.completed",
                "workflow.completed",
                "workflow.failed",
                "artifact.created",
            ],
        },
        "browser_session": {
            "enabled": True,
            "mode": "delegated_session",
            "session_field": "browser_session_id",
            "auth_mode_field": "auth_mode",
        },
        "output_profiles": {
            "enabled": True,
            "source": "web_crawler.uiOutputProfiles",
        },
        "tool_form": {
            "source": "web_crawler.ui",
            "chat_input": "web_crawler.uiChatInput",
            "group_labels": "web_crawler.uiGroupLabelMap",
            "profiles": "web_crawler.uiOutputProfiles",
        },
    },
    title="Crawler Operation",
    summary=(
        "A form-agent crawler role that exposes a crawl operation backed by "
        "a workflow: crawl, normalize, store, and return structured references."
    ),
    description=(
        "The Crawler role runs inside the generic Form Agent interface. "
        "It renders the Web Crawler Tool as a structured form, but submits the "
        "user-facing crawl operation to a workflow. The crawler tool itself "
        "only performs crawling; normalization, storing, and output handling "
        "are workflow steps."
    ),
    emoji="🕷️",
    icon="lucide:radar",
    color="emerald",
    tags=[
        "crawler",
        "form-agent",
        "operation-role",
        "workflow-driven",
        "web-crawling",
        "structured-reporting",
        "browser-session",
    ],
    badges=[
        "workflow-driven",
        "tool-form",
    ],
    docs={
        "overview": "/docs/roles/crawler#overview",
        "usage": "/docs/roles/crawler#usage",
        "operations": "/docs/roles/crawler#operations",
        "changelog": "/docs/roles/crawler#changelog",
    },
    links={
        "source": "https://example.com/repo/roles/crawler",
    },
    config_schema={
        "type": "object",
        "properties": {
            "layout": {
                "type": "string",
                "enum": ["tool_form"],
                "default": "tool_form",
            },
            "role_adapter": {
                "type": "string",
                "const": "crawler",
            },
            "default_operation": {
                "type": "string",
                "const": "crawl",
            },
            "form_source": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["tool"],
                    },
                    "key": {
                        "type": "string",
                        "const": "web_crawler",
                    },
                },
                "required": ["type", "key"],
                "additionalProperties": False,
            },
            "submit_target": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["workflow"],
                    },
                    "key": {
                        "type": "string",
                        "default": "crawl_operation_v1",
                    },
                },
                "required": ["type", "key"],
                "additionalProperties": False,
            },
            "submit_mode": {
                "type": "string",
                "enum": ["execute_workflow"],
                "default": "execute_workflow",
            },
            "result_renderer": {
                "type": "string",
                "enum": ["auto", "crawler", "json", "artifact"],
                "default": "crawler",
            },
            "browser_session": {
                "type": "string",
                "enum": ["enabled", "disabled", "auto"],
                "default": "enabled",
            },
            "output_profiles": {
                "type": "string",
                "enum": ["enabled", "disabled", "auto"],
                "default": "enabled",
            },
            "sections": {
                "type": "object",
                "properties": {
                    "target": {"type": "boolean", "default": True},
                    "profile": {"type": "boolean", "default": True},
                    "filters": {"type": "boolean", "default": True},
                    "advanced": {"type": "boolean", "default": True},
                    "results": {"type": "boolean", "default": True},
                    "logs": {"type": "boolean", "default": True},
                    "artifacts": {"type": "boolean", "default": True},
                },
                "additionalProperties": False,
            },
            "crawl_operation": {
                "type": "object",
                "properties": {
                    "workflow_key": {
                        "type": "string",
                        "default": "crawl_operation_v1",
                    },
                    "store_outputs": {
                        "type": "boolean",
                        "default": True,
                    },
                    "normalize_reports": {
                        "type": "boolean",
                        "default": True,
                    },
                    "return_artifacts": {
                        "type": "boolean",
                        "default": True,
                    },
                },
                "additionalProperties": False,
            },
        },
        "required": [
            "layout",
            "role_adapter",
            "default_operation",
            "form_source",
            "submit_target",
            "submit_mode",
        ],
        "additionalProperties": False,
    },
    default_config={
        "layout": "tool_form",
        "role_adapter": "crawler",
        "default_operation": "crawl",
        "form_source": {
            "type": "tool",
            "key": "web_crawler",
        },
        "submit_target": {
            "type": "workflow",
            "key": "crawl_operation_v1",
        },
        "submit_mode": "execute_workflow",
        "result_renderer": "crawler",
        "browser_session": "enabled",
        "output_profiles": "enabled",
        "sections": {
            "target": True,
            "profile": True,
            "filters": True,
            "advanced": True,
            "results": True,
            "logs": True,
            "artifacts": True,
        },
        "crawl_operation": {
            "workflow_key": "crawl_operation_v1",
            "store_outputs": True,
            "normalize_reports": True,
            "return_artifacts": True,
        },
    },
    safety={
        "allowed": [
            "public website crawling",
            "authenticated delegated-session crawling",
            "structured crawl reporting",
            "document and media discovery",
            "technical SEO inspection",
        ],
        "blocked": [
            "credential collection",
            "secret extraction",
            "bypassing access controls",
            "high-volume scraping without limits",
            "destructive actions on target websites",
        ],
        "guardrails": {
            "same_domain_default": True,
            "max_depth_default": 1,
            "max_pages_default": 5,
            "requires_delegated_session_for_authenticated_targets": True,
            "no_user_password_collection": True,
            "respect_tool_limits": True,
        },
    },
    compat={
        "min_app_version": "0.9.0",
        "deprecated": False,
    },
    telemetry={
        "metrics": [
            "latency_ms",
            "workflow_latency_ms",
            "crawl_duration_ms",
            "pages_crawled",
            "reports_generated",
            "documents_found",
            "media_found",
            "stored_outputs",
            "tool_usage",
            "workflow_steps",
            "browser_session_events",
        ],
    },
    state={
        "memory_strategy": "short_term",
        "keys": [
            "operation",
            "form_values",
            "crawl_plan",
            "crawl_result",
            "normalized_reports",
            "stored_output",
            "artifact_refs",
            "errors",
        ],
    },
    compute={
        "tier": "standard",
        "max_tokens": 4096,
        "est_tokens_per_turn": 600,
    },
    phase_specs={
        "analyze": PhaseSpec(
            title="Analyze crawl request",
            summary=(
                "Validate the submitted crawl operation, target URL, profile, "
                "auth mode, and required runtime capabilities."
            ),
            timeout_ms=2000,
            emits=[
                "operation",
                "validated_inputs",
                "capability_requirements",
            ],
            ui_hints={
                "show_intermediate_panel": False,
            },
        ),
        "plan": PhaseSpec(
            title="Resolve crawl workflow",
            summary=(
                "Resolve the crawl operation to its configured workflow runner "
                "and prepare workflow inputs from the form values."
            ),
            timeout_ms=3000,
            consumes=[
                "validated_inputs",
            ],
            emits=[
                "workflow_plan",
                "workflow_inputs",
            ],
            ui_hints={
                "show_plan_panel": False,
            },
        ),
        "task": PhaseSpec(
            title="Run crawl operation",
            summary=(
                "Execute the crawl workflow: crawl the target, normalize reports, "
                "store outputs, and collect result references."
            ),
            timeout_ms=180000,
            consumes=[
                "workflow_inputs",
            ],
            emits=[
                "crawl_result",
                "normalized_reports",
                "stored_output",
                "artifact_refs",
            ],
            ui_hints={
                "show_progress": True,
                "show_runtime_events": True,
            },
        ),
        "refine": PhaseSpec(
            title="Format operation result",
            summary=(
                "Prepare a user-facing operation summary from stored outputs, "
                "artifacts, and workflow result metadata."
            ),
            timeout_ms=3000,
            consumes=[
                "crawl_result",
                "normalized_reports",
                "stored_output",
                "artifact_refs",
            ],
            emits=[
                "operation_summary",
            ],
        ),
        "evaluate": PhaseSpec(
            title="Evaluate operation result",
            summary=(
                "Check whether the crawl operation completed, whether reports "
                "were generated, and whether outputs were stored successfully."
            ),
            timeout_ms=2000,
            consumes=[
                "operation_summary",
                "stored_output",
            ],
            emits=[
                "score",
                "final_output",
            ],
            ui_hints={
                "show_score_badge": False,
                "show_completion_status": True,
            },
        ),
    },
)