# AgentVerse Agents - BaseAgent, Agent Classes, and How to Create One

*Audience:* AgentVerse developers  
*Goal:* Explain how `BaseAgent` works, how agent **classes** (like `ChatAgent`) are composed, and how to create your own agent class and spec cleanly.

---

## Table of Contents

- [AgentVerse Agents - BaseAgent, Agent Classes, and How to Create One](#agentverse-agents--baseagent-agent-classes-and-how-to-create-one)
  - [Table of Contents](#table-of-contents)
  - [Mental Model](#mental-model)
  - [BaseAgent Anatomy](#baseagent-anatomy)
    - [Key responsibilities](#key-responsibilities)
    - [Interface properties](#interface-properties)
  - [Mixins Overview](#mixins-overview)
  - [History \& Memory Model](#history--memory-model)
    - [Canonical history (source of truth)](#canonical-history-source-of-truth)
    - [General chat stream (optional UI channel)](#general-chat-stream-optional-ui-channel)
    - [Memory keys](#memory-keys)
  - [Tool Routing vs General Chat](#tool-routing-vs-general-chat)
  - [AgentSpec \& Registry Binding](#agentspec--registry-binding)
  - [ChatAgent Walkthrough](#chatagent-walkthrough)
  - [Create Your Own Agent Class (Step-by-Step)](#create-your-own-agent-class-step-by-step)
    - [1) Define an `AgentSpec`](#1-define-an-agentspec)
    - [2) Implement the subclass](#2-implement-the-subclass)
    - [3) Register \& wire resources](#3-register--wire-resources)
    - [4) Test the agent](#4-test-the-agent)
  - [Best Practices \& Gotchas](#best-practices--gotchas)

---

## Mental Model

An **Agent** is a **composition** of:
- a **core** (`BaseAgent`) providing shared state and wiring,
- a set of **mixins** (history, memory, tools, prompts, etc.),
- an **AgentSpec** (declarative) registered with the **Agent Registry**, and
- an optional **subclass** that implements behavior (e.g., `respond()` orchestration).

The **UI** and **runtime** use the spec for discoverability/config, while the subclass implements runtime logic.

---

## BaseAgent Anatomy

`BaseAgent` (excerpt):
```python
class BaseAgent(
    CapabilitiesMixin, LifecycleMixin, LLMCallsMixin, MemoryMixin,
    AgentInterface, KnowledgeAgentMixin, ABC,
):
    def __init__(
        id: str, user_id: str, creator: str, name: str, system_name: str,
        type: str, prompt: str, soul: AgentSoulProtocol, llm: Any,
        image: Any = None, video: Any = None,
        audio: Any = None, db: Any = None, cache: Any = None, vectordb: Any = None,
        commbridge: Any = None, messaging: Any = None, collections: Optional[List[str]] = None,
        chat_url: Optional[str] = None, personality_key: Optional[str] = None,
        dna_sequence: Optional[str] = None, access_mode: str = "public",
        public_key: Optional[str] = None, private_key: Optional[str] = None,
        whitelist_users: Optional[List[str]] = None, blacklist_users: Optional[List[str]] = None,
        tools: Optional[List[ToolSpec | Dict[str, Any]]] = None, metadata: Optional[Dict[str, Any]] = None, **kwargs
    ):
        ...
```

### Key responsibilities
- **Identity & access**: `id`, `user_id`, `creator`, `access_mode`, keys/whitelists/blacklists.
- **Personality & prompt**: `AgentFactory` resolves `personality_key` (the canonical personality
  registry key) into a `soul` via `resolve_personality(...)` and injects it; the agent turns that
  soul into `personality_context` via `generate_personality_context(...)`. The agent never reaches
  into the personality registry itself - `personality_key` is retained as identity only, for the
  per-turn override path in the LLM invoker.
- **Backends**: `llm`, `db`, `cache`, `vectordb`, `messaging`, `commbridge` injected here.
- **Capabilities discovery**: detects `vision`, `audio`, `video`, `memory`, `rag`, `messaging` based on injected backends.
- **Runtime bindings**: `request`, `default_session_id`, `tenant_id` (set via orchestrator).
- **Tools wiring**: builds `ToolManager` and `ToolRouter` from `tool_specs` (lazy import to avoid circulars).

### Interface properties
Implements the `AgentInterface` contract via properties:
```python
@property def id(self) -> str: ...
@property def user_id(self) -> str: ...
@property def name(self) -> str: ...
@property def type(self) -> str: ...
```

---

## Mixins Overview

Each mixin owns a single responsibility. Highlights:

- **LifecycleMixin** - spawn/cleanup hooks, readiness checks, warm-up.
- **MemoryMixin** - `remember(name, value, ttl)`, `recall(name)`, `forget(name)` with Redis-backed keys (AEI-friendly).
- **CapabilitiesMixin** - what the agent advertises it can do.
- **LLMCallsMixin** - the shared LLM call paths.
- **KnowledgeAgentMixin** - RAG/connections (if present).

Session history is *not* a mixin. It lives in the Conversation module
(`src/domains/agentverse/history/conversation.py`) - see
[agentverse-conversation-history.md](agentverse-conversation-history.md).

> Keep agent behavior thin: orchestrate in your subclass, and delegate to mixins.

---

## History & Memory Model

### Canonical history (source of truth)
- Key pattern (example):  
  `api-template:...:history:v1:<user_id>:<agent_id>:<session_id>`
- Contains **everything**: `user`, `assistant`, `tool_call`, `tool_result`, `system`…
- The LLM should usually read from **canonical** to keep context (tools, system notes).

### General chat stream (optional UI channel)
- If you need a clean chat-only view, use `tool="general"` or a dedicated `chat:` key.
- Write to **canonical** always; write to **general** only when **no tool** handled the turn.
- When using “general” mode, you may build the LLM context from that stream to keep it “pure chat”.

### Memory keys
- Examples: `...:mem:v1:<user_id>:<agent_id>:active_collection:<session_id>`
- Use `MemoryMixin.remember/recall/forget`, not raw cache ops.

---

## Tool Routing vs General Chat

**Flow:**
1. Append **user** to **canonical**.
2. Try `_process_tools(...)` with a **canonical** history slice.
3. If a tool handled the turn, echo its user-visible result as an **assistant** entry (canonical) and **return**.
4. Otherwise, route to **general chat**:
   - write `user` to `tool="general"`,
   - build context from `tool="general"`,
   - generate LLM reply,
   - append **assistant** to both canonical and general.

**Important:** In the agent, prefer `if tool_reply is not None:` (not truthy) so empty strings still count as handled tool turns.

---

## AgentSpec & Registry Binding

Your spec advertises the agent to the platform and front-end. Example (abridged from `chat_spec`):

```python
chat_spec = AgentSpec(
    key="chat",
    name="Chat",
    description="Conversational agent with friendly tone, short-term memory, and optional tool use.",
    version="1.1.0",
    capabilities=["text-chat","memory","tool-use"],
    io=IOSchema(...),
    persona_profile="GenericEVAProfile",
    prompt=PromptProfile(system="You are a warm, precise assistant...", model_prefs={"model":"gpt-4o-mini","temperature":0.6}),
    memory=MemoryProfile(retention_ttl_sec=3600, pii_redaction=True),
    tools=[ToolRef(name="Datetime Tool", provider_key="tools:datetime", permissions=["read:datetime"])],
    resources=ResourceBindings(needs=["llm","redis"], providers={"llm":"llm:openai","redis":"infra:redis"}),
    spawn=SpawnPolicy(singleton=False, prewarm=True, terminate_on_idle_ms=180_000),
    safety=SafetyPolicy(moderation="standard-v2", allow_tools=["Datetime Tool"]),
    budget=BudgetPolicy(tokens_per_call=3000, rpm_limit=120, cost_cap_usd=2.0),
    telemetry=TelemetryProfile(log_level="INFO"),
    default_config={"style":"friendly","enable_tools":True},
    config_schema={ ... },
    ui_schema=UISchema(...)
)
```

**Bind the subclass to the spec:**
```python
@agent_registry_instance.bind_spec(chat_spec)
class ChatAgent(BaseAgent):
    ...
```

This connects the spec (metadata/config) with the class (runtime behavior). The agent is
registered under `spec.key` (the canonical key everything resolves by) and displayed as
`spec.name`. The full serialized spec is exposed as the registry item's `metadata`, so the base
registry is the single source of truth - there is no separate spec store.

---

## ChatAgent Walkthrough

```python
@agent_registry_instance.bind_spec(chat_spec)
class ChatAgent(BaseAgent):
    async def respond(self, user_input: str, file: Any = None, session_id: str | None = None, tool_kwargs: dict[str, Any] | None = None) -> dict[str, Any]:
        sid = session_id
        clean_user_input, user_flags = self._parse_user_input(user_input)

        # 1) load the replay window for this session
        conversation = await self._load_conversation(session_id=sid)

        # 2) run the orchestrator over it
        ctx = await self._execute_orchestrator(
            spec=self._resolve_role_spec(),
            clean_user_input=clean_user_input,
            raw_user_input=user_input,
            session_id=sid,
            conversation=conversation,
            ...
        )

        # 3) append the turn only once it produced something
        await conversation.append_user(user_input, rendered=ctx.get("rendered_user"))
        reply = self._build_reply(ctx)
        await conversation.append_assistant(_assistant_history_text_from_reply(reply))
        return reply
```

**Why it’s clean**
- All appends happen exactly once, through `Conversation`, and only after the turn has run.
- The agent never touches Redis keys or window sizes; `Conversation.load` owns both.
- A failed turn records the user message and nothing else, so a retry sees an unbroken conversation.

---

## Create Your Own Agent Class (Step-by-Step)

### 1) Define an `AgentSpec`
- Name, description, version.
- IO schema for validation.
- Persona/prompt (system/style/model).
- Memory, tools, resources, spawn, safety, budget, telemetry.
- Default config + JSON schema for UI forms.

```python
from src.domains.agentverse.agents.specs.agent_spec import AgentSpec, PromptProfile, IOSchema, ...

my_spec = AgentSpec(
    key="researcher",
    name="Researcher",
    description="Deep-dive research agent with long-context summarization.",
    version="0.1.0",
    capabilities=["text-chat","rag","memory","tool-use"],
    io=IOSchema(...),
    prompt=PromptProfile(
        system="You are an analytical research assistant...",
        style_guide="- Cite sources\n- Be precise\n",
        model_prefs={"model":"gpt-4o","temperature":0.4}
    ),
    memory=MemoryProfile(retention_ttl_sec=7200),
    tools=[ToolRef(name="RAG Tool", provider_key="rag", permissions=["read:knowledge"])],
    resources=ResourceBindings(needs=["llm","redis","vectordb"], providers={"llm":"llm:openai","redis":"infra:redis","vectordb":"kb:chroma"}),
    spawn=SpawnPolicy(prewarm=True),
    safety=SafetyPolicy(moderation="standard-v2"),
    budget=BudgetPolicy(tokens_per_call=5000, rpm_limit=60, cost_cap_usd=3.0),
    telemetry=TelemetryProfile(log_level="INFO"),
    default_config={"enable_tools": True},
    config_schema={"type":"object","properties":{"enable_tools":{"type":"boolean"}},"required":["enable_tools"]},
)
```

### 2) Implement the subclass
- Inherit from `BaseAgent`
- - Optionally override prompt composition or add domain logic.

```python
from src.domains.agentverse.agents.base import BaseAgent
from src.domains.agentverse.history.conversation import Conversation
from src.domains.agentverse.registries.registries import agent_registry_instance

@agent_registry_instance.bind_spec(my_spec)
class ResearchAgent(BaseAgent):
    async def respond(self, user_input: str, file: Any = None, session_id: str | None = None, tool_kwargs: dict | None = None) -> str:
        sid = session_id
        conversation = await Conversation.load(
            cache=self.cache, request=self.request,
            user_id=self.user_id, agent_id=self.id, session_id=sid,
        )

        tool_reply = await self._process_tools(
            user_input, conversation.entries(), file, session_id=sid, tool_kwargs=tool_kwargs
        )
        if tool_reply is not None:
            await conversation.append_user(user_input)
            await conversation.append_assistant(tool_reply)
            return tool_reply
```

### 3) Register & wire resources
- Your DI must provide `llm`, `cache`/`redis`, optional `vectordb`, etc.
- The `ToolManager`/`ToolRouter` are auto-built from `tool_specs` passed to the agent constructor.

### 4) Test the agent
Use `anyio` tests with fake `llm` and `vectordb` to cover both paths (tool vs general).

---

## Best Practices & Gotchas

- **Always echo an assistant message** after a tool-handled turn.  
- Use `if tool_reply is not None` (not truthy) so empty strings still count as handled tool turns.
- Let `Conversation` bound the context. The replay window is global (`redis_history_window`); do not add a per-agent one.
- Keep subclass logic thin; push specialized behavior into mixins or services.
- Avoid circular imports by lazily importing `ToolManager`/`ToolRouter` as in `BaseAgent`.

---

Happy building! With `BaseAgent` + mixins + spec binding, you can spin up new, well-behaved agents in minutes while keeping history, memory, and tools consistent.