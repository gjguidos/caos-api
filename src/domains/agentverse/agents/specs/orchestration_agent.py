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
    ToolRef,
)

orchestration_spec = AgentSpec(
    ownership="global",
    key="orchestration",
    name="Orchestration",
    description="Deterministic workflow-runner agent pinned to a single workflow.",
    version="1.0.0",
    department="AR",
    tags=["workflow", "orchestration", "deterministic"],
    capabilities=["workflow-orchestration", "tool-use"],
    agent_group=AgentGroup.ORCHESTRATION_META,
    agent_subclass=AgentSubclass.MULTI_AGENT_COORDINATOR,
    prompt=PromptProfile(
        system=(
            "You run deterministic workflows. "
            "When the user sends @workflow commands, route them to the workflow tool."
        ),
        model_prefs={"model": "gpt-4o-mini", "temperature": 0.0},
    ),
    memory=MemoryProfile(
        stm_window=4,
        ltm_collection=None,
        embedding_model=None,
        retention_ttl_sec=3600,
        pii_redaction=False,
    ),
    tools=[
        ToolRef(
            key="workflow",
            name="Workflow Tool",
            provider_key="tools:workflow",
        )
    ],
    resources=ResourceBindings(
        needs=["llm", "redis"],
        providers={
            "llm": "llm:openai",
            "redis": "redis:primary",
        },
    ),
    spawn=SpawnPolicy(
        singleton=False,
        max_pool_size=4,
        prewarm=False,
        timeout_ms=120_000,
        terminate_on_idle_ms=300_000,
    ),
    safety=SafetyPolicy(
        moderation=None,
        allow_tools=["Workflow Tool"],
        deny_topics=[],
        pii_handling="allow",
    ),
    budget=BudgetPolicy(
        tokens_per_call=500,
        tokens_per_session=5_000,
        rpm_limit=30,
    ),
    telemetry=TelemetryProfile(
        log_level="INFO",
        metrics_tags={"agent": "orchestration", "dept": "AR"},
    ),
    release=ReleaseChannel(channel="stable", traffic_weight=100),
)
