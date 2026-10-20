from src.domains.agentverse.agents.specs.agent_spec import (
    AgentGroup,
    AgentSpec,
    AgentSubclass,
    BudgetPolicy,
    EvaluationProbe,
    IOSchema,
    MemoryProfile,
    PromptProfile,
    ReleaseChannel,
    ResourceBindings,
    SafetyPolicy,
    SpawnPolicy,
    TelemetryProfile,
    ToolRef,
    UISchema,
)

chat_spec = AgentSpec(
    key="chat",
    name="Chat",
    description="Conversational agent with friendly tone, short-term memory, and optional tool use.",
    version="1.1.0",
    department="AR",
    tags=["default", "friendly", "low-latency"],
    capabilities=["text-chat", "memory", "tool-use"],
    io=IOSchema(
        input_jsonschema={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
        output_jsonschema={
            "type": "object",
            "properties": {
                "reply": {"type": "string"},
                "citations": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["reply"],
        },
    ),
    persona_profile="GenericEVAProfile",
    prompt=PromptProfile(
        system="You are a warm, precise assistant. Prefer concise answers with helpful extras when relevant.",
        style_guide="- Friendly tone\n- Tech-savvy\n- Avoid purple prose\n",
        model_prefs={"model": "gpt-4o-mini", "temperature": 0.6},
    ),
    memory=MemoryProfile(
        stm_window=16,
        ltm_collection=None,  # no vector store by default
        embedding_model=None,
        retention_ttl_sec=3600,
        pii_redaction=True,
    ),
    agent_group=AgentGroup.INTERACTIVE_UI,
    agent_subclass=AgentSubclass.CONVERSATIONAL,
    tools=[
        ToolRef(
            name="Datetime Tool",
            provider_key="tools:datetime",
            permissions=["read:datetime"],
        )
    ],
    resources=ResourceBindings(
        needs=["llm", "redis", "image"],
        providers={
            "llm": "llm:openai",  # your DI keys
            "redis": "redis:primary",
            "image": "image:openai",
        },
    ),
    spawn=SpawnPolicy(
        singleton=False,
        max_pool_size=8,
        prewarm=True,
        timeout_ms=25_000,
        terminate_on_idle_ms=180_000,
    ),
    safety=SafetyPolicy(
        moderation="standard-v2",
        allow_tools=["Datetime Tool"],
        deny_topics=[],
        pii_handling="mask",
    ),
    budget=BudgetPolicy(
        tokens_per_call=3000, tokens_per_session=30_000, rpm_limit=120, cost_cap_usd=2.0
    ),
    telemetry=TelemetryProfile(
        log_level="INFO",
        trace_sample_rate=0.2,
        metrics_tags={"agent": "Chat", "dept": "AR"},
        # dashboard_url can be filled by your Grafana board
    ),
    eval_probes=[
        EvaluationProbe(
            name="contract:io",
            kind="contract",
            params={"input_required": ["message"], "output_required": ["reply"]},
            acceptance={"strict": True},
        ),
        EvaluationProbe(
            name="latency:p50",
            kind="latency",
            params={"max_ms": 1200},
            acceptance={"p50<=ms": 1200},
        ),
        EvaluationProbe(
            name="safety:moderation",
            kind="safety",
            params={"tests": ["harassment", "self-harm", "pii"]},
            acceptance={"violations": 0},
        ),
    ],
    release=ReleaseChannel(channel="stable", traffic_weight=100),
    default_config={"max_history": 16, "style": "friendly", "enable_tools": True},
    config_schema={
        "type": "object",
        "properties": {
            "max_history": {"type": "integer", "minimum": 0, "maximum": 100},
            "style": {"type": "string", "enum": ["friendly", "neutral", "formal"]},
            "enable_tools": {"type": "boolean"},
        },
        "required": ["max_history", "enable_tools"],
    },
    ui_schema=UISchema(
        order=["style", "max_history", "enable_tools"],
        widgets={"style": "select"},
        helptext={
            "style": "Tone for responses",
            "max_history": "Short-term memory window (messages)",
        },
    ),
)
