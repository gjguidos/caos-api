from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator

from src.domains.agentverse.utils.canonicalize import require_canonical_key

Semver = str  # enforce semver separately


class AgentGroup(str, Enum):
    COGNITIVE_CORE = "cognitive_core"  # 1
    CREATIVE_GENERATIVE = "creative_generative"  # 2
    ANALYTICAL_RESEARCH = "analytical_research"  # 3
    INTERACTIVE_UI = "interactive_ui"  # 4
    OPERATIONAL_EXEC = "operational_exec"  # 5
    SENSORY_PERCEPTION = "sensory_perception"  # 6
    GUARDIAN_COMPLIANCE = "guardian_compliance"  # 7
    ORCHESTRATION_META = "orchestration_meta"  # 8
    DOMAIN_SPECIALIST = "domain_specialist"  # 9
    EMOTIONAL_SOCIAL = "emotional_social"  # 10


class AgentSubclass(str, Enum):
    # 1 Cognitive Core
    REASONING = "reasoning"
    KNOWLEDGE_RAG = "knowledge_rag"
    PLANNING = "planning"
    OPTIMIZATION = "optimization"
    # 2 Creative & Generative
    NARRATIVE = "narrative"
    DESIGN = "design"
    CODE_GEN = "code_gen"
    MUSIC_SOUND = "music_sound"
    # 3 Analytical & Research
    DATA_SCIENCE = "data_sci"
    MARKET_RESEARCH = "market_research"
    SCIENTIFIC_RESEARCH = "scientific_research"
    OPERATIONAL = "operational"
    SIMULATION = "simulation"
    # 4 Interactive & Interface
    CONVERSATIONAL = "conversational"
    TRANSLATION = "translation"
    TRAINING_TEACHING = "training_teaching"
    NEGOTIATOR = "negotiator"
    # 5 Operational & Execution
    TASK_AUTOMATION = "task_automation"
    SYSTEM_ADMIN = "system_admin"
    PROCUREMENT = "procurement"
    EVENT_MANAGEMENT = "event_management"
    # 6 Sensory & Perception
    VISION = "vision"
    AUDIO = "audio"
    SENSOR_FUSION = "sensor_fusion"
    DOC_UNDERSTANDING = "doc_understanding"
    # 7 Guardian & Compliance
    SECURITY = "security"
    COMPLIANCE = "compliance"
    ETHICS = "ethics"
    SAFETY_ORCHESTRATION = "safety_orchestration"
    # 8 Orchestration & Meta
    MULTI_AGENT_COORDINATOR = "multi_agent_coordinator"
    PERFORMANCE_OPTIMIZER = "performance_optimizer"
    CONFLICT_RESOLVER = "conflict_resolver"
    LIFECYCLE_MANAGER = "lifecycle_manager"
    # 9 Domain Specialists
    MEDICAL_ADVISOR = "medical_advisor"
    LEGAL_ADVISOR = "legal_advisor"
    FINANCIAL_ANALYST = "financial_analyst"
    ENGINEERING_CAD = "engineering_cad"
    # 10 Emotional & Social
    THERAPEUTIC = "therapeutic"
    COMPANION = "companion"
    ROLEPLAY = "roleplay"
    CULTURAL_LIAISON = "cultural_liaison"


class ToolRef(BaseModel):
    name: str
    version: Semver | None = None
    provider_key: str = Field(..., description="DI key, e.g. 'tools:search'")
    permissions: list[str] = Field(default_factory=list)
    default_config: dict[str, Any] = Field(default_factory=dict)


class PromptProfile(BaseModel):
    system: str
    style_guide: str | None = None
    few_shots: list[dict[str, str]] = Field(default_factory=list)
    model_prefs: dict[str, Any] = Field(
        default_factory=lambda: {
            "model": "gpt-4o-mini",
            "temperature": 0.7,
        }  # could the gpt-4o-mini be a default?
    )


class MemoryProfile(BaseModel):
    stm_window: int = 12  # messages
    ltm_collection: str | None = None  # vector namespace
    embedding_model: str | None = None  # e.g. "text-embedding-3-large"
    retention_ttl_sec: int | None = 86_400
    pii_redaction: bool = True


class IOSchema(BaseModel):
    input_jsonschema: dict[str, Any] = Field(default_factory=dict)
    output_jsonschema: dict[str, Any] = Field(default_factory=dict)


class ResourceBindings(BaseModel):
    needs: list[str] = Field(
        default_factory=list,
        description="abstract deps, e.g. ['redis','mongo','vector', 'llm']",
    )
    providers: dict[str, str] = Field(
        default_factory=dict,
        description="abstract -> DI key, e.g. {'redis': 'infra:redis', 'llm': 'llm:openai'}",
    )


class SpawnPolicy(BaseModel):
    singleton: bool = False
    max_pool_size: int = 4
    prewarm: bool = False
    timeout_ms: int = 30_000
    terminate_on_idle_ms: int = 120_000


class SafetyPolicy(BaseModel):
    moderation: str | None = "standard-v2"
    allow_tools: list[str] | None = None  # whitelist
    deny_topics: list[str] = Field(default_factory=list)
    pii_handling: Literal["block", "mask", "allow"] = "mask"


class BudgetPolicy(BaseModel):
    tokens_per_call: int = 4_000
    tokens_per_session: int = 40_000
    rpm_limit: int | None = None
    cost_cap_usd: float | None = None


class TelemetryProfile(BaseModel):
    log_level: Literal["DEBUG", "INFO", "WARN", "ERROR"] = "INFO"
    trace_sample_rate: float = 0.1
    metrics_tags: dict[str, str] = Field(default_factory=dict)
    dashboard_url: HttpUrl | None = None


class EvaluationProbe(BaseModel):
    name: str
    kind: Literal["contract", "prompt-regression", "safety", "latency"]
    params: dict[str, Any] = Field(default_factory=dict)
    acceptance: dict[str, Any] = Field(default_factory=dict)


class ReleaseChannel(BaseModel):
    channel: Literal["stable", "beta", "canary"] = "stable"
    traffic_weight: int = 100
    compatible_min_app: Semver | None = None


class UISchema(BaseModel):
    # Hints for auto-generated config forms (you mentioned wanting this)
    order: list[str] = Field(default_factory=list)
    widgets: dict[str, str] = Field(default_factory=dict)  # key -> 'textarea'|'password'|...
    helptext: dict[str, str] = Field(default_factory=dict)


class AgentSpec(BaseModel):
    # Identity
    key: str  # canonical, unique identifier (e.g. "chat"); the FE addresses agents by this
    name: str
    description: str
    version: Semver
    department: str = "AR"
    tags: list[str] = Field(default_factory=list)

    agent_group: AgentGroup
    agent_subclass: AgentSubclass

    # Capabilities & IO
    capabilities: list[str] = Field(default_factory=list)
    io: IOSchema = IOSchema()

    # Persona / Prompt / Memory
    persona_profile: str | None = None  # e.g. "GenericEVAProfile" or "MortalEVA"
    prompt: PromptProfile
    memory: MemoryProfile = MemoryProfile()

    # Tools & Resources
    tools: list[ToolRef] = Field(default_factory=list)
    resources: ResourceBindings = ResourceBindings()
    spawn: SpawnPolicy = SpawnPolicy()

    # Policy, Budget, Telemetry, Eval, Release
    safety: SafetyPolicy = SafetyPolicy()
    budget: BudgetPolicy = BudgetPolicy()
    telemetry: TelemetryProfile = TelemetryProfile()
    eval_probes: list[EvaluationProbe] = Field(default_factory=list)
    release: ReleaseChannel = ReleaseChannel()

    # Config + UI schema for frontends
    default_config: dict[str, Any] = Field(default_factory=dict)
    config_schema: dict[str, Any] = Field(default_factory=dict)  # JSON Schema_validate_spec
    ui_schema: UISchema = UISchema()

    @model_validator(mode="after")
    def _key_is_canonical(self) -> AgentSpec:
        require_canonical_key(self.key, kind="Agent", name=self.name)
        return self
