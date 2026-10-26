from src.domains.agentverse.agents.roles.compliance_auditor.handlers.h_analyze import h_analyze
from src.domains.agentverse.agents.roles.compliance_auditor.handlers.h_evaluate import h_evaluate
from src.domains.agentverse.agents.roles.compliance_auditor.handlers.h_plan import h_plan
from src.domains.agentverse.agents.roles.compliance_auditor.handlers.h_refine import h_refine
from src.domains.agentverse.agents.roles.compliance_auditor.handlers.h_task import h_task
from src.domains.agentverse.agents.roles.role_spec import PhaseSpec, RolePolicy, RoleSpec

role_spec = RoleSpec(
    ownership="global",
    key="compliance_auditor",
    name="Compliance Auditor",
    version="1.1.0",
    policy=RolePolicy(name="compliance_auditor", temperature=0.3, samples=1, quality_threshold=0.8),
    handlers={
        "analyze": h_analyze,
        "plan": h_plan,
        "task": h_task,
        "refine": h_refine,
        "evaluate": h_evaluate,
    },
    agent_group="interactive_ui",
    agent_subclass="conversational",
    default_tools=["datetime"],
    capabilities={
        "routing": "tools-first",
        "chat_fallback": True,
        "refine_loop": "one-refine-max",
        "system_tools": {
            "analyze": "analyze",
            "refine": "refine",
            "evaluate": "evaluate",
        },
        "required_tools": {"green_claim": "green_claim"},
    },
    title="Compliance Auditor Orchestrator",
    summary="A safe, tools-first writer Compliance Auditor that plans, executes, refines, and self-evaluates.",
    description=(
        "The General role follows the universal loop - Analyze → Plan → Task → Refine → Evaluate. "
        "It prioritizes tools as ideation tool over chat."
    ),
    emoji="🧭",
    icon="lucide:goal",
    color="indigo",
    tags=["orchestrator", "compliance_auditor", "multi-tool", "safe-by-default"],
    badges=["recommended"],
    docs={
        "overview": "/docs/roles/compliance_auditor#overview",
        "usage": "/docs/roles/compliance_auditor#usage",
        "changelog": "/docs/roles/compliance_auditor#changelog",
    },
    links={"source": "https://example.com/repo/roles/compliance_auditor"},
    config_schema={
        "type": "object",
        "properties": {
            "temperature": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.3},
            "quality_threshold": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.8},
            "allow_intermediate_render": {"type": "boolean", "default": True},
            "offer_next_step": {"type": "boolean", "default": True},
            "cta_style": {"type": "string", "enum": ["inline", "panel"], "default": "panel"},
            "cta_persona": {"type": "string", "default": "friendly, helpful, not pushy"},
            "cta_max_words": {"type": "number", "minimum": 8, "maximum": 40, "default": 22},
            "locale": {"type": "string", "default": "en"},
        },
        "required": [],
    },
    default_config={
        "temperature": 0.3,
        "quality_threshold": 0.8,
        "allow_intermediate_render": True,
        "offer_next_step": True,
        "cta_style": "panel",
        "cta_persona": "friendly, helpful, not pushy",
        "cta_max_words": 22,
        "locale": "en",
    },
    safety={
        "allowed": ["general knowledge", "light reasoning", "summarization"],
        "blocked": [
            "medical diagnosis",
            "legal advice",
            "financial advice without disclaimers",
        ],
        "guardrails": {"max_refine_loops": 1},
    },
    compat={"min_app_version": "0.9.0", "deprecated": False},
    telemetry={"metrics": ["latency_ms", "eval_score", "refine_loops", "tool_usage"]},
    state={"memory_strategy": "short_term", "keys": ["plan", "draft", "evidence", "rubric"]},
    compute={"tier": "standard", "max_tokens": 4096, "est_tokens_per_turn": 800},
    phase_specs={
        "analyze": PhaseSpec(
            title="Analyze",
            summary="Read the ask, identify goals, constraints, and missing info.",
            timeout_ms=2000,
            emits=["analysis"],
            ui_hints={"show_intermediate_panel": True},
        ),
        "plan": PhaseSpec(
            title="Plan",
            summary="Propose a concrete, tool-first plan and confirm critical assumptions.",
            timeout_ms=3000,
            consumes=["analysis"],
            emits=["plan"],
            ui_hints={"show_plan_panel": True},
        ),
        "task": PhaseSpec(
            title="Task",
            summary="Execute plan steps and call tools. Fall back to chat if a tool is unavailable.",
            timeout_ms=15000,
            consumes=["plan"],
            emits=["work_products", "evidence"],
            ui_hints={"show_progress": True},
        ),
        "refine": PhaseSpec(
            title="Refine",
            summary="Tighten the output based on rubric and user constraints.",
            timeout_ms=3000,
            consumes=["work_products", "evidence"],
            emits=["refined_output"],
        ),
        "evaluate": PhaseSpec(
            title="Evaluate",
            summary="Score against the quality threshold and decide if a single refine loop is needed.",
            timeout_ms=2000,
            consumes=["refined_output"],
            emits=["score", "final_output"],
            ui_hints={"show_score_badge": True},
        ),
    },
)
