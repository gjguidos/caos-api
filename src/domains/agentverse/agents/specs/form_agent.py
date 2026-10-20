from src.domains.agentverse.agents.specs.agent_spec import (
    AgentGroup,
    AgentSpec,
    AgentSubclass,
    BudgetPolicy,
    MemoryProfile,
    PromptProfile,
    ReleaseChannel,
    ResourceBindings,
    SafetyPolicy,
    SpawnPolicy,
    TelemetryProfile,
)

form_agent_spec = AgentSpec(
    key="form",
    name="Form",
    description=(
        "Generic operational agent type that exposes one or more registered "
        "tools through a structured form-based interface with a result panel."
    ),
    version="1.0.0",
    department="AR",
    tags=[
        "form-agent",
        "tool-form",
        "structured-ui",
        "tool-execution",
        "workflow-ready",
        "agent-runtime",
    ],
    capabilities=[
        "tool-use",
        "structured-form-ui",
        "tool-execution",
        "structured-results",
        "runtime-events",
    ],
    agent_group=AgentGroup.INTERACTIVE_UI,
    agent_subclass=AgentSubclass.OPERATIONAL,
    persona_profile="GenericEVAProfile",
    prompt=PromptProfile(
        system=(
            "You are a generic operational form agent. Execute the configured "
            "primary tool using the submitted form values. Return tool results "
            "as structured output and do not invent missing execution data."
        ),
        model_prefs={
            "model": "gpt-4o-mini",
            "temperature": 0.1,
        },
    ),
    memory=MemoryProfile(
        stm_window=4,
        ltm_collection=None,
        embedding_model=None,
        retention_ttl_sec=1800,
        pii_redaction=True,
    ),
    tools=[
        # Generic form agent can be configured with different tools.
        # The concrete role/config decides which tool is primary.
        #
        # Example role:
        # ToolRef(
        #     name="Web Crawler Tool",
        #     provider_key="tools:web_crawler",
        #     permissions=[
        #         "network:http:read",
        #         "crawler:execute",
        #         "crawler:reports:read",
        #         "browser-session:consume",
        #     ],
        # )
    ],
    resources=ResourceBindings(
        needs=[
            "redis",
        ],
        providers={
            "redis": "redis:primary",
        },
    ),
    spawn=SpawnPolicy(
        singleton=False,
        max_pool_size=8,
        prewarm=False,
        timeout_ms=180_000,
        terminate_on_idle_ms=300_000,
    ),
    safety=SafetyPolicy(
        moderation="standard-v2",
        allow_tools=[
            # Filled by the concrete role/config.
        ],
        deny_topics=[],
        pii_handling="mask",
    ),
    budget=BudgetPolicy(
        tokens_per_call=1500,
        tokens_per_session=10_000,
        rpm_limit=30,
        cost_cap_usd=1.0,
    ),
    telemetry=TelemetryProfile(
        log_level="INFO",
        trace_sample_rate=1.0,
        metrics_tags={
            "agent": "Form Agent",
            "dept": "AR",
            "interface": "tool-form",
            "operation": "tool-execution",
        },
    ),
    release=ReleaseChannel(
        channel="canary",
        traffic_weight=100,
    ),
    default_config={
        "layout": "tool_form",
        "submit_mode": "execute_tool",
        "primary_tool": None,
        "tool_resolution": "role_primary_or_first_form_tool",
        "result_renderer": "auto",
        "runtime_events": True,
        "sections": {
            "profile": True,
            "filters": True,
            "advanced": True,
            "results": True,
            "logs": True,
            "artifacts": True,
        },
        "capabilities": {
            "browser_session": "auto",
            "output_profiles": "auto",
            "tool_actions": "auto",
        },
    },
    config_schema={
        "type": "object",
        "properties": {
            "layout": {
                "type": "string",
                "enum": ["tool_form"],
            },
            "submit_mode": {
                "type": "string",
                "enum": [
                    "execute_tool",
                    "chat_then_execute",
                    "workflow_execute",
                ],
            },
            "primary_tool": {
                "type": ["string", "null"],
                "description": (
                    "Optional primary tool key. If omitted, the client/runtime "
                    "resolves the first available tool exposing form UI metadata."
                ),
            },
            "tool_resolution": {
                "type": "string",
                "enum": [
                    "role_primary_or_first_form_tool",
                    "first_form_tool",
                    "explicit_only",
                ],
            },
            "result_renderer": {
                "type": "string",
                "enum": [
                    "auto",
                    "json",
                    "markdown",
                    "artifact",
                    "custom",
                ],
            },
            "runtime_events": {
                "type": "boolean",
            },
            "sections": {
                "type": "object",
                "properties": {
                    "profile": {"type": "boolean"},
                    "filters": {"type": "boolean"},
                    "advanced": {"type": "boolean"},
                    "results": {"type": "boolean"},
                    "logs": {"type": "boolean"},
                    "artifacts": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
            "capabilities": {
                "type": "object",
                "properties": {
                    "browser_session": {
                        "type": "string",
                        "enum": ["auto", "enabled", "disabled"],
                    },
                    "output_profiles": {
                        "type": "string",
                        "enum": ["auto", "enabled", "disabled"],
                    },
                    "tool_actions": {
                        "type": "string",
                        "enum": ["auto", "enabled", "disabled"],
                    },
                },
                "additionalProperties": False,
            },
        },
        "required": [
            "layout",
            "submit_mode",
            "tool_resolution",
            "result_renderer",
            "runtime_events",
        ],
        "additionalProperties": False,
    },
)