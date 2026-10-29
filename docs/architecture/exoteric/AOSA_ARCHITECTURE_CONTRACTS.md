# AOSA Architecture Contracts

Canonical description of the **Agent, Tool/Action, Workflow, Validation and Governance contracts** that CAOS code and coding agents must respect. It complements the [AOSA Engineering Principles](AOSA_ENGINEERING_PRINCIPLES.md) (how to decide) and the [architecture knowledge index](../README.md) (where the evidence lives). Current milestone, drift and next work are tracked separately in [Implementation Status](../IMPLEMENTATION_STATUS.md).

**Status labels.** Every statement below is one of:

- **Implemented** — present in source on the reviewed revision, with tests cited.
- **Partial** — present but incomplete; the gap is named.
- **Proposed** — intended architecture; *not* a field, API or behavior that exists.

Reviewed revision: `feature/ops-v2` at `ac36d84` plus uncommitted G1.4 (2026-10-29). Recheck source before relying on any claim; this document is not runtime policy, a registry or an authorization source. Repository paths are root-relative; `agentverse/` abbreviates `src/domains/agentverse/`.

---

## 1. Agent Contract

### 1.1 Identity and persisted definition — Implemented

- The persisted definition is the existing Pydantic **`Agent`** model (`agentverse/entities/agent.py`) stored in MongoDB `coll_agents`. Do not introduce a replacement agent schema.
- Composition fields: `type → role_name → personality_key → rule_pack_ids`, plus `tools: list[AgentToolRef]`, `workflows: list[str]` (catalog keys the agent may run) and resource back-end selectors (`llm_type`, `audio_type`, …).
- Server-owned fields: `id`, `creator`, `ownership`, `tenant_id`, `exposure`, `runtime_version` (`"v1"` default for historical records, `"v2"` explicit). Client requests (`AgentRequest`, `AgentUpdateRequest`) reject or strip them; `genesis_local_seed` is trusted-bootstrap provenance only.
- **System Citizens (uncommitted):** every Cognitive Agent is a System Citizen, but not every System Citizen is a Cognitive Agent. Deterministic citizens (local JOSHUA and Genesis) are declared in the reviewed policy (`system_citizens`, `SystemCitizen` in `agentverse/agents/lifecycle/agent_principal_resolver.py`) and have no `coll_agents` definition. The same UTU resolver substitutes the trusted record for the definition only; session, membership, approval, permit, revocation and CPBX grants are unchanged. A citizen must have an approval and can never be a provisioned/handoff target; it grants nothing and cannot join conversations (no Cognitive definition).
- **Bootstrap lease and authorization origin (uncommitted):** UTU authority always comes from a durable session record. Besides operator sessions (JWT-bound), the reviewed policy may declare a `bootstrap` entry: a trusted, short-lived startup lease (`RedisAgentSessionAuthority.bootstrap_lease`) for exactly one handoff's two System Citizens, never a Cognitive Agent. It is not a login (no token; no bearer, ticket or WebSocket can adopt it), it obeys membership fences, durable expiry and UTU revocation, and it is revoked when the startup task ends. UTU binds the record's `origin` into every `ExecutionPermit` and rechecks it; `AuthorizationProvenance.origin` (`session` | `bootstrap`, default omitted when serialized) reaches Seshat and Store, so `subject_id` reads as the authenticated requester for `session` and as the policy-designated owner for `bootstrap`.
- `AgentRequest` (`agentverse/entities/agent_request.py`) is the V1 client creation input. It is **not** strict (`extra="allow"`); see the [Validation Contract](#4-validation-contract).

### 1.2 Agent Soul and personality — Implemented

- A personality key resolves through `PersonalityProfileRegistry` to an `AgentSoulProtocol` instance (`agentverse/registries/personality.py`, `agentverse/personalities/utils/resolve_personality.py`).
- `PromptMixin.build_system_prompt_from_soul()` renders the soul into system instructions; V1 and V2 both use it.
- `resolve_personality()` falls back to the default soul for an *absent* key. V2 `CognitiveResponseV2` additionally fails closed on an explicit but unregistered key.
- Agent Soul economic/wallet fields carry no authority and are not wired to execution.

### 1.3 Governed communication interface — Implemented (two distinct runtimes)

> **Every CAOS agent has a governed communication interface. This is independent of whether Chat is exposed or selectable in the client.**

Communication is part of what an agent *is*, not an optional tool the user happens to attach. The two runtimes realise it differently and must not be presented as identical:

| | V1 (`runtime_version="v1"`) | V2 (`runtime_version="v2"`) |
| --- | --- | --- |
| Equipping | `AgentFactory._ensure_core_tools()` always adds `CORE_TOOL_KEYS = ("chat", "datetime", "cta")` (`agentverse/agents/factory.py`), regardless of request or role | No tool instantiation; capabilities are UTU invoke grants on the agent principal |
| Turn execution | `agent.respond()` through the legacy UniversalOrchestrator/context path and V1 `ToolManager` | `ClientEventDispatcherV2` → `Khepri.awaken` → `ManagedAgentV2.ncsc.request("chat.response" \| "chat.context")` through CPBX |
| Model call | Chat tool + LLM invoker inside the V1 pipeline | `CognitiveResponseV2` (`agentverse/services/cognitive_response_v2.py`): one governed inference; soul + role + rule packs + `prompt`; **no tools, no workflows, no external actions** |
| Creation self-test | `DivineOrchestrationService.create_agent` calls `AgentChatService.test_agent()`, which spawns the agent and runs `agent.respond("Hello. Can you hear me?")`, then sleeps it | None yet; a V2 definition becomes conversational only after approval, grants and activation |
| Gate | WebSocket user + V1 access checks | Verified session, UTU permit, `chat.context` grant for the conversation and `chat.response` grant for LLM mode; definition must be Chat or the approved `form/enki` composition, `runtime_version="v2"`, `creator == session user` |

`chat.context` (`agentverse/tools/chat_tool_v2/synaptic.py`) is registered at import. `chat.response` is registered at Genesis startup only when the reviewed policy grants it (`agentverse/dependencies/genesis_bootstrap.py`).

### 1.4 Optional assigned tools — Implemented (V1); Proposed (V2)

- V1: `Agent.tools` holds slim `AgentToolRef{key, config}` references. The factory expands them through the tool registry into `ToolSpec`s and merges, in order: explicit refs, core tools, role default tools, role system tools, workflow-required tools (`scope="workflow"`).
- V2: assigned tools are not executed. `CognitiveResponseV2` returns `composition_unavailable` if the role requires tools. Tool use in V2 is **proposed** to go through resolved synaptic actions and CPBX (see §2).

### 1.5 Runtime composition — Implemented

- V1: `AgentService.get_runtime_agent()` → `AgentFactory` builds a `BaseAgent` with cognitive resources, soul, role, rule packs and the tool set above.
- V2: `AgentFactoryV2.build(key)` returns a minimal `ManagedAgentV2` bound to a `TrustedNCSCConnector`. It does **not** compose tools, activate, register or claim ownership. Composition for a reply is resolved per turn by `CognitiveResponseV2` from the stored definition and registries.

### 1.6 Resource ownership and governance — Implemented

- `ownership ∈ {"tenant", "global", "system"}` with explicit `tenant_id` (`agentverse/entities/resource_ownership.py`). Missing/invalid ownership is unclassified and fails closed; ownership is never inferred from names, creators or approvals.
- `exposure` (catalog visibility) and `visibility`/`whitelisted_groups` (read access within scope) are independent of invocation rights (`UTUAuthority.can_access_resource`).
- V1 creation sets `ownership="tenant"`, `tenant_id=user.tenant_id`, `exposure="public"`, `creator=user.id`, and requires `agent_access.can_create` (verified tenant + admin or group admin).

### 1.7 Lifecycle and activation — Implemented (V2, static policy)

- `Khepri.awaken` / `Anubis.sleep` delegate to `AgentLifecycleService` (`agentverse/agents/lifecycle/`): instance lock, `UTUAuthority.resolve_agent_principal`, presence lease, `AgentFactoryV2`.
- Activation requires a server-provisioned `AgentActivationApproval` (owner must equal the session user and the definition `creator`) and the tenant's installed UTU policy version. Approvals and grants are **a static startup snapshot** from `GENESIS_SECURITY_POLICY_FILE` (`config/genesis.local.json`).
- **Partial:** a newly persisted agent cannot be activated until approvals/grants are provisioned through a reviewed, persisted policy path. This is the main prerequisite for Genesis agent creation.

---

## 2. Tool and Action Contract

### 2.1 Implemented fields

| Contract | Location | Implemented meaning |
| --- | --- | --- |
| `ToolSpec` | `agentverse/tools/spec.py` | Tool identity (`key`, `name`, `version`, `tool_class`), invocation `scope` (`agent`/`workflow`/`system`), catalog `exposure` (`public`/`internal`), `config`, `ui`, `outputs: list[ToolOutputSpec]`, `supports`, `tags`, `enabled`, `source`, and optional `synaptic` |
| `SynapticToolSpec` / `SynapticCapabilitySpec` | `agentverse/tools/spec.py` | Declared capabilities: `signal` (exact dotted address), `access ∈ {core, granted, system}`, `trusted_lines: list[str]` |
| `AgentToolRef` | `agentverse/tools/spec.py` | Persisted per-agent reference: canonical `key` + agent-specific `config`; unknown fields dropped |
| `ToolRegistry` | `agentverse/registries/tool_registry.py` | Catalog of tool classes + metadata (V1 `ToolManager` source) |
| `SynapticRegistry` / `SynapticBinding` | `agentverse/registries/synaptic_registry.py` | Exact `signal@version → (provider, handler)` bindings; conflicting providers rejected |
| `SynapticExchange` (CPBX) | `agentverse/ncsc/cpbx.py` | Resolves the binding, enforces policy and provider target, issues the child context with lineage |

### 2.2 Rules (Implemented)

- **Tools are registered capabilities.** Declaration (`ToolSpec`), execution (`BaseTool`/handler) and authorization (UTU/CPBX grants) are separate.
- **Persist references, not callables.** Agents persist `AgentToolRef` keys; workflows persist tool keys in steps; handlers are resolved at runtime through the registry and the governed runtime (V1 `ToolManager`, V2 CPBX).
- **Assignment never confers authorization.** Attaching a tool, a ToolSpec `scope`, catalog exposure, `SynapticCapabilitySpec.access` or registry presence grants nothing. Invocation requires a UTU/CPBX root grant or explicit trusted line for the specific principal and tenant.
- **System tools stay out of client catalogs.** Non-public exposure (`internal`, legacy system/builtin/hidden flags via `classify_resource`) is filtered from client projections (`RegistryService`, `manifest_projection`; see [RG-02F](../../REGISTRY_GOVERNANCE_V1_RG02F.md)). Internal dependencies may satisfy composition without disclosure.
- **Synaptic-only privileged capabilities are absent from the V1 catalog.** `store.create` (`agentverse/tools/store_tool/`) is registered only in `SynapticRegistry`, never in `ToolRegistry`, so V1 `ToolManager` cannot reach it.

### 2.3 Partial

- `ToolSpec.synaptic` declarations are **descriptive**: nothing cross-checks them against `SynapticRegistry` bindings or derives grants from them.
- There is no persisted link from an `AgentToolRef` to the synaptic signals ("actions") it makes available.

### 2.4 Proposed schema extensions (not implemented)

Proposed, to be reviewed before implementation (use `.agents/skills/aosa-tool-spec/SKILL.md`):

- `SynapticCapabilitySpec.version` and an input/output contract reference, so an action is `signal@version` with a typed payload.
- Action resolution: assigning a tool resolves its available actions from the registered `ToolSpec.synaptic` declarations; a declaration with no matching `SynapticBinding` fails validation.
- Optional explicit action bindings on `AgentToolRef` (e.g. `actions: list[{signal, version}]`), validated as a subset of the tool's declared actions, persisted as references only.
- Grants remain a separate, reviewed UTU policy step; resolving or binding an action never provisions a grant.

---

## 3. Workflow Contract

### 3.1 Implemented

- **Definition:** `WorkflowSpec` (`agentverse/workflows/spec.py`): `key`, `name`, `version`, `definition` (validated into `WorkflowDefinition` at runtime), `tags`, `inputs: list[WorkflowInputField]`, `ui: dict`.
- **Steps:** `WorkflowStep` (`agentverse/tools/workflow_tool/models/__init__.py`): `id`, `type`, `command` (V1 tool command string), `depends_on`, `file_bindings`, `pause`, `set`, `wait_for`, `retry`.
- **Execution:** `WorkflowTool` → `WorkflowEngine.run_until_pause_or_done` → `stable_tool_runner` → V1 `ToolManager`. Runs are resolved **by catalog key only** (no inline DAG), the key must be in the agent's attached `workflows`, resume uses the stored definition snapshot, and per-step `evaluate_policy` still applies. Run state lives in a session-scoped Redis store with a TTL.
- **Inputs vs presentation:** `inputs` declares run inputs using the same field vocabulary as tool `ui.admin.fields`; `ui` is presentation metadata (icon, category). Reserved input keys are rejected.

### 3.2 Partial / known gaps

- Only declared **file** inputs are validated server-side at `op=start`. `required`, `constraints` and `options` on other declared inputs are currently **presentation hints**, not enforced. Presentation must not be treated as authoritative validation.
- Steps call tools by templated command strings, not structured arguments; a step cannot pin a tool or capability version.
- Workflow execution is governed by V1 agent/workflow attachment and tool policy, not by UTU/CPBX grants. Run state is not durable across sessions.

### 3.3 Proposed — UO2 (Universal Orchestrator V2)

**UO2 is the evolution of the existing WFT engine, not a competing engine.** It is proposed and does not exist in source. Intended contract:

- Workflows coordinate **registered capabilities** (`signal@version`) through a CPBX tool runner bound to the workflow's authorized synaptic client; nested calls require explicit trusted lines.
- Structured, typed step `args` alongside the legacy command path; exact version pinning of the definition and its capabilities.
- `WorkflowSpec.inputs` becomes an enforced server-side input contract; `WorkflowSpec.ui` stays presentation-only and never duplicates or replaces validation.
- Durable/deferred execution depends on OPS V2 (OPS2-04C.2: background authority, workers, fenced commit). A workflow can never construct or alter trusted authorization context.

The V1 `UniversalOrchestrator` (fixed analyze → plan → task → refine → evaluate) is a separate legacy component; UO2 does not extend it.

---

## 4. Validation Contract

**Implemented (agent resource, `ac36d84`).** `data.validation@1.0.0` (provider `system.validation`, `agentverse/tools/validation_tool/`; tests `agentverse/tests/tools/test_data_validation_tool.py`) is synaptic-only, read-only and registered at Genesis startup only when the reviewed policy grants it or has a trusted line to it. The requester is the CPBX-issued permit subject. Points 1–5 below hold for `resource_type="agent"`; point 6 is unchanged (the Store Tool does not call it).

1. **Persisted target:** the existing `Agent` model is the persisted definition. Validation produces a normalized `Agent`-compatible definition; it never writes.
2. **Strict input contract:** agent creation input is validated by a strict (`extra="forbid"`) contract that excludes server-owned fields (`id`, `ownership`, `tenant_id`, `creator`, `exposure`, `runtime_version`, seed provenance). `AgentRequest` (`extra="allow"`) is not that contract.
3. **Registry resolution:** types, roles, personalities, rule packs, workflows, tools and (proposed) actions resolve through existing registries/catalogs, filtered by the requester's read access (`UTUAuthority.can_access_resource`). Unknown or unreadable references fail closed.
4. **RulePacks** contribute contextual constraints (role and pack rules) to validation and later cognition; they are not permissions.
5. **Validation grants nothing.** A valid definition is not an approval, grant, activation or ownership decision.
6. **Defense in depth:** the Store Tool independently enforces trusted persistence requirements (server-derived ownership, explicit `runtime_version`, rejection of unknown/spoofed fields, idempotency) regardless of upstream validation.

---

## 5. Governance Contract

### 5.1 Responsibilities

| Component | Implemented responsibility | Not its responsibility |
| --- | --- | --- |
| **UTU** (`agentverse/security/utu.py`) | Resource read/discover decisions, revocable execution permits, session binding, lifecycle authorization, policy install (CAS) and revocation; delegates grant evaluation to `SynapticAuthorization` | Routing, execution, persistence |
| **NCSC / CPBX** (`agentverse/ncsc/`) | Resolve exact bindings, enforce supplied policy (root grant + trusted lines), provider target, lineage, execution gate and permit watch | Cognitive recipes, workflow orchestration, deciding policy |
| **Enlil** (OPS V2, `src/base/operations/v2/`) | **Partial:** durable admission, managed fencing and bounded discovery (`operations.dispatch.discover`, provider `system.enlil`, no production caller yet) | Executing admitted work, minting authority (workers/recovery are proposed, OPS2-04C.2+) |
| **Seshat** (`agentverse/audit/`, `coll_logs`) | Cognitive Audit and Provenance Service: append-only, tenant-isolated, idempotent audit records (`CAOSLogRecord` v1) attributed only from UTU permit provenance; event-specific metadata allowlists; trusted in-process service | Authorization, grants, reading for agents/clients (no API), execution, atomicity with the audited effect |
| **JOSHUA initiation** (`agentverse/agents/genesis/initiation.py`; G1.4 uncommitted) | Explicit local-only initiation grant and server-pinned handoff; current UTU approvals for both principals, mandatory initiation evidence, separate Genesis client, correlated result through the existing V2 signal interface | Store/validation grants, forging permits, redefining Genesis's chain, activating Enki |
| **Genesis provisioner** (`agentverse/agents/genesis/`) | Coordinates `data.validation` → `AgentComposer` → `store.create.provisioned` through CPBX with the Genesis principal's client, under one OPS V2 managed operation, recording each confirmed outcome with Seshat; resolves its own interrupted attempts via OPS V2 takeover + `resolve_reconciled` and re-executes idempotently | Constructing permits, adding grants, activation (Khepri), asserting outcomes it did not observe |
| **Khepri** (`agentverse/agents/lifecycle/khepri.py`) | Agent activation through `AgentLifecycleService` (Anubis deactivates) | Granting capabilities, creating definitions |
| **Store Tool** (`store.create`, `store.create.provisioned`) | Idempotent, insert-only persistence of approved resources (currently `agent`) with provenance; `store.create.provisioned` creates only tenant-owned identities pinned in the reviewed Genesis policy (`provisioned` slots: fixed ID, principal, subject, tenant) and confers no general `store.create` | Validation of references, approvals, activation, system ownership (still reserved) |

**Form-first Enki V2 (uncommitted):** presentation and conversation are independent.
The local slot pins `type="form"`; registered role `enki` is operational and requires
no executable tools. V2 composition and `chat.response` support only the reviewed
`form/enki` pair in addition to existing Chat Agents, with unchanged UTU/Soul/backend
checks. This is not generic Form execution or a creation grant. The version-1
`caos.agent_creation_draft` contract prepares local previews only; authoritative
registry validation and privileged submission require a future approved capability.
Historical chat creation requests fail the current pin before successful-operation
replay. Existing persisted definitions and historical evidence are not patched.

### 5.2 Distinct decisions — never collapse them

| Decision | Mechanism (implemented) | Grants invocation? |
| --- | --- | --- |
| **Registration** | `ToolRegistry`, `SynapticRegistry`, `WorkflowRegistry` | No |
| **Discovery** | Client catalog projections + `can_access_resource(…, "DISCOVER")` | No |
| **Exposure** | `exposure` / `classify_resource` | No |
| **Attachment** | `Agent.tools`, `Agent.workflows` | No (V1 attachment scopes V1 tool use only) |
| **Invocation authorization** | UTU permit + `SynapticAuthorization` root grant / trusted line, enforced by CPBX on every call | Yes, per principal, tenant and signal |
| **Lifecycle activation** | Approval + installed policy version + `Khepri.awaken` | No (activation is not a grant) |

Signed events, event names, agent IDs or names, symbolic roles and LLM output are never authorization. Capability authorization is not blanket authorization for every resource a payload names; resource checks remain path-specific.

### 5.3 Audit — Seshat's operation-centric pattern

Seshat is the Cognitive Audit and Provenance Service. Its records are **evidence**, never authorization, and its producer contract (`AuditSink` in `agentverse/audit/service.py`) hides storage so ingestion can change without changing producers.

**Implemented (L1 + L2, `ac36d84`; G1.4 initiation uncommitted):**

- **What is audited.** Only authoritative lifecycle transitions, mandatory governance decisions, consequential effects and terminal outcomes, written by the trusted coordinator that owns the operation. CPBX synapses, child contexts, worker messages and tool invocations are **not** persisted automatically. Genesis writes three execution records on success, plus G1.4's initiation evidence per distinct initiating authorization:

  | Event | Classification |
  | --- | --- |
  | `genesis.initiation.authorized` | G1.4 governance decision: verified JOSHUA/user, server-pinned Genesis executor and Enki target; event version 1, durable before provisioning |
  | `genesis.validation.completed` | Governance decision (definition admissible for the requester) |
  | `genesis.composition.completed` | Governance decision (trusted V2 policy: runtime version, backend selection) |
  | `genesis.store.completed` | Consequential effect + terminal success evidence |
  | `genesis.store.failed` | Consequential effect outcome (rejected/failed) |

- **Mutable state lives in OPS V2.** Progress, attempts, leases and retry intent are the managed operation (`operations_v2`); there is no shared mutable audit document.
- **G1.4 initiation is separate authority, not impersonation.** `genesis.enki.request@1.0.0` (`system.genesis`, `agentverse/agents/genesis/initiation.py`) is registered only by local bootstrap. Static JOSHUA has only the initiation grant; the reviewed tenant/user/JOSHUA → Genesis → Enki binding is server-owned. The trusted adapter re-resolves both current approvals through UTU and obtains a separate Genesis client for the same live session/user. CPBX never changes the original principal through a trusted line. Legacy Joshu-A is unchanged; Enki is not activated.
- **Initiator evidence is not execution evidence.** The initiation record attributes JOSHUA and the authorizing user; the three successful execution records attribute Genesis. Shared operation identity links these facts without relabelling execution provenance. The initiation ID is stable per operation and initiating authorization digest, so retries reuse it; a new authenticated session records a new authorization fact. Existing successful execution evidence may be reused after re-authentication only for the same identity, policy and logical fact; its original provenance remains immutable.
- **Correlation without per-event documents.** `correlation_id` is the operation ID (also passed to every CPBX request); audit causation chains operation → validation → composition → store. Internal events are correlated, not individually stored.
- **Small, immutable, idempotent, independently insertable.** One document per fact, identity on the built-in `_id`, no updates; every field is bounded and the whole record is capped at 16 KiB (`MAX_RECORD_BYTES`).
- **Stable identities.** A successful logical step has one ID per operation (an idempotent retry reuses it); a rejection/failure has one ID per attempt (repeated failures stay distinct).
- **Mandatory audit is synchronous and observable.** `append` returns only after durable acceptance or raises a typed, retryable error; a producer never reports success without its evidence (Genesis leaves the operation retry-pending).

**Deferred until required (not implemented):** distributed/asynchronous ingestion (any such path must preserve durable acknowledgement for mandatory audit — no fire-and-forget), batching, partitioning/sharding of `coll_logs`, retention, and protected artifact storage with versioned manifests for large or high-cardinality evidence (records would reference artifacts by digest, never embed them).

---

## Maintenance

Update this document when a contract changes in source, keeping Implemented / Partial / Proposed accurate. Record consequential decisions as ADRs under [decisions/](../decisions/). Update [Implementation Status](../IMPLEMENTATION_STATUS.md) for milestones and drift; do not duplicate subsystem detail that lives in existing docs and skills.
