# AOSA Engineering Principles

**Authority:** Canonical engineering-review guidance for AOSA/CAOS, consolidated for
manual review at the project owner's direction. README/index summaries should link
here rather than maintain competing lists.

**Scope:** Practical design and delivery principles, not a runtime inventory or policy
engine. The principles state engineering direction; specific implementations still need
explicit contracts, review and evidence. Existing accepted ADRs and subsystem contracts
retain their scope. Surface conflicts for a decision; do not silently supersede them.

**Reviewed baseline:** `feature/ops-v2`, `f3de0d6`, 2026-10-28. Separate implemented
behavior, executed tests, accepted decisions, proposals and unknowns. A principle or
archetype name is not proof that a component is deployed.

## Principles

### 1. Tool-first internal processes

Before adding an internal process, investigate whether an existing or new **versioned,
registered, deterministic tool** can express the responsibility. A tool is a governed
capability interface, not necessarily an agent, LLM invocation, process or microservice.
Prefer deterministic behavior where appropriate; cognitive capabilities remain possible
behind explicit contracts. Registration describes availability, not permission.

Keep kernel-critical bootstrap, authorization enforcement, atomic state invariants,
execution fencing and essential recovery mechanisms in trusted primitives when ordinary
tool execution would weaken security or create a circular dependency. Document the reason
for such an exception.

### 2. Minimal trusted kernel

Keep the trusted kernel limited to mechanisms required to establish and preserve identity,
authority, resource lifecycle and correctness invariants. Put domain behavior and cognitive
recipes behind governed capabilities. A tool may depend on trusted primitives; it must
not redefine their authority. A new manager, supervisor or privileged service needs a
specific responsibility that existing components cannot safely fulfill.

### 3. Scalability by design, optimization by measurement

Design independent operations, agents, channels and capabilities to scale horizontally
without changing their logical identities or fundamentally redesigning their contracts.
Prefer explicit ownership, bounded queues/tasks/connections, backpressure and replaceable
adapters. State expected load and failure assumptions.

Do not add caching, partitioning, scheduling, concurrency frameworks or distributed
optimizations without demonstrated requirements. Measure latency, throughput, contention,
resource use and failure behavior before choosing an optimization. Neither an unbounded
prototype nor speculative distributed complexity is a scalability strategy.

### 4. Proven patterns, justified responsibilities

Use established patterns where they solve an identified problem; record their guarantees,
costs and simpler alternatives. Dependency injection, repositories, atomic conditional
writes, idempotent consumers and outboxes are options, not mandatory layers everywhere.

Keep responsibilities distinct: UTU owns permission decisions; NCSC/CPBX resolves/routes
and enforces supplied invocation policy; Enlil supervises operations; Khepri handles agent
lifecycle; WFT owns workflow semantics; future THOTH supports evidence-based inspection.
This division guides design, not a claim that every named runtime is implemented. Avoid
a universal orchestrator that absorbs these boundaries.

### 5. Transport-independent capability invocation

Define logical capability identity, typed inputs/outputs, versioning and error behavior
independently of REST, WebSockets, local calls or messaging. Prefer an authorized local
call when a distributed hop adds no value. Use events/workers when distribution or
durability requires them, with equivalent security boundaries—not an assumption that
all transports have identical latency, ordering or delivery semantics.

### 6. Replaceable implementations and deployment independence

A tool's implementation may be local, worker-dispatched or hosted by an independent
service without changing its logical interface. Version incompatible behavior and make
deployment/executable compatibility explicit; never silently substitute the latest provider.
Keep cognitive resources replaceable across model providers. Logical composition does not
require a microservice per tool, and independent deployment is not mandatory distribution.

Declaring a tool version does not establish executable retention. The appropriate runtime
must enforce exact version resolution, availability and compatibility; metadata alone
does not prove that a recorded executable can be reconstructed.

### 7. Deterministic governance; optional LLM reasoning

Identity resolution, permissions and hard execution invariants must not depend on LLM
judgment. Use reasoning for tasks where it adds demonstrable value; the platform must
remain useful for deterministic integration, retrieval, crawling and workflows. Treat
RulePacks, personality and prompts as declarative resources, not shadow security policy.
Mandatory requirements need actual trusted enforcement, not merely prompt instructions.

### 8. Explicit identity, tenant isolation and authorization

Derive identity, tenant and delegated scope from trusted context. Apply default-deny checks
at invocation and resource-access boundaries, including nested calls and recovery. Shared
context or knowledge does not imply shared private memory or access rights. An event name,
registry entry, owner string, fencing token or signed envelope is not a permission grant.
Preserve lineage and honor revocation using existing UTU/NCSC boundaries; do not invent
another authorization subsystem for each capability.

### 9. Independent agent, channel and worker lifecycles

Separate durable logical identity/context from physical processes, worker incarnations,
sessions and client connections. A channel's persistence, an agent's activation and a
worker's execution lease have different owners and lifetimes. Reconnection must not
imply a new identity; disconnect must not silently decide durable-work cancellation.
Independent lifetime never extends authorization: explicit permits, expiration, cleanup
and shutdown draining still apply.

### 10. Event-driven communication; durable state where required

Events coordinate; services and tools implement behavior. Declare delivery, ordering and
acknowledgement semantics. When correctness depends on remembering admitted work, outcomes
or cancellation intent, retain authoritative durable state and define discovery after
interruption. Broker delivery and WebSocket/Pub/Sub notification are not durable operation
truth. Choose discovery, outbox or another established pattern for a concrete guarantee,
not because every interaction must become an event.

### 11. Vertical-slice delivery toward working capabilities

Prioritize the first working **`#weather` channel**: persistent channel context, Adam and
specialist agents, shared sourced knowledge, and governed capability interaction. This is
the delivery target, **not a claim that the complete channel or those participants are
already provisioned**. Validate persistence, source attribution, isolation and continuity
through an end-to-end slice rather than collecting disconnected infrastructure milestones.

Enlil and other foundations should implement the smallest prerequisites that advance this
slice while preserving correctness. Workflow/crawler workloads remain useful supporting
validation where needed; do not require a complete generic runtime, workflow engine or
Genesis framework before demonstrating product value. Preserve existing V1 behavior and
use explicit opt-in adapters/migrations rather than opportunistic rewrites.

### 12. Observability, provenance and explainability

Make executable behavior operationally visible. Preserve operation/attempt identity,
correlation/causation, capability/data versions, sources, authorization decisions and
outcome evidence across boundaries. Distinguish observations, model interpretations and
decisions; communicate uncertainty and verification limits. Persist shared sourced knowledge
and relevant continuity rather than hiding them inside isolated prompts. Use redaction,
retention and access controls instead of logging every prompt, secret or private payload.

### 13. Explicit concurrency, consistency, recovery and effects

State which invariants are atomic, which store is authoritative and what can be stale.
Define ownership, bounded execution, cancellation, interruption, duplicate delivery,
retry/backoff and reconciliation behavior. Where execution fencing is required, bind
current identity/attempt, generation, revision and unexpired authority to the authoritative
mutation—not a separate lease precheck. Preserve history and handle ambiguous writes.

A cancellation request or lease expiry does not prove quiescence. Reconciliation ownership
is not permission to replay unresolved work. Persistence/queue acknowledgements do not
prove exactly-once external effects: require capability-specific sink fencing/idempotency
or explicit uncertainty/manual review. Verify the chosen backend's actual guarantees;
MongoDB behavior does not establish CosmosDB compatibility or failover safety.

### 14. Reuse before new infrastructure or orchestration

Inspect existing CAOS contracts, DI providers, specs, registries, clients and lifecycle
hooks before adding infrastructure, services, registries or orchestration layers. Reuse
their ownership and security boundaries; extend narrowly when evidence identifies a gap.
Do not duplicate the broker, tool catalog, authorization engine or workflow supervisor.
Reuse is not blind compatibility: expose limitations and justify changes rather than
assuming inherited V1 behavior already satisfies a new distributed contract.

### 15. Architectural self-application — CAOS uses CAOS

Whenever practical, implement CAOS internal processes using the same versioned, registered
and governed tool capabilities provided to users, agents, channels, integrations and
workflows. This complements tool-first design and reuse; it does not require every helper
function to become a tool or every local call to become distributed.

Internal capabilities should:

- Reuse existing AOSA tool specifications and capability registries.
- Expose stable, versioned input/output contracts.
- Be independently testable and observable.
- Be composable through WFT when multi-step execution is required, without duplicating
  workflow semantics in a new controller or assuming a proposed WFT runtime exists.
- Support deterministic execution by default and optional LLM-powered cognition where appropriate.
- Preserve trusted identity, authorization, resource scope, correlation and causation.
- Remain independent of physical execution placement.
- Support implementation replacement without changing their logical capability identity,
  with explicit versioning of incompatible behavior.

**Architectural validation:** Use these capabilities for real internal operations.
Successful composition and operation provide practical evidence for AOSA's design;
record the exercised contracts, versions, outcomes and limits rather than inferring
universal correctness from one demonstration. This is a validation direction, not a claim
that all internal processes already use governed tools.

**Kernel exception:** Trusted bootstrap, authorization enforcement, atomic state
invariants, execution fencing and essential recovery must not depend on ordinary tool
execution where doing so introduces circular dependencies or weakens security. Retain
the [tool-first exception](#1-tool-first-internal-processes), not a tool-shaped bypass.

**Versioning qualification:** Apply [principle 6](#6-replaceable-implementations-and-deployment-independence):
a declared version is not executable retention. The runtime must enforce exact resolution,
availability and compatibility. Self-application does not justify a new registry,
mandatory distributed layer or optimization without measured evidence.

### 16. Cognitive OS Recursivity — Agents as Cognitive Operating Environments

**An agent is a miniature cognitive OS:** a cognitive operating environment that composes
governed capabilities around persistent Agent Soul and identity. Its tools are its
**registered, versioned applications**, not necessarily separate processes or LLM calls.
Agent Soul and logical identity persist independently of runtime placement, worker
incarnation or client connection; reconstruction and activation still require explicit
lifecycle and authorization checks.

Agents can both **consume and expose capabilities**. A tool may be implemented by another
agent, a workflow, a service or a deterministic function, while retaining its logical
capability identity and contract. This permits recursive composition without requiring
every agent to expose every internal tool or every capability to become an agent.
LLM reasoning is optional, **including for first-class system citizens**; neither citizen
identity nor this cognitive-OS model requires an autonomous reasoning loop.

**UTU authorization remains authoritative at every composition boundary.** Preserve
trusted principal and tenant context, delegated authority, resource scope, correlation
and causation through nested calls. Exposing a capability, invoking another agent or
sharing context must not widen permissions. NCSC/CPBX enforces its supplied invocation
policy; recursive composition cannot bypass that enforcement or resource-access checks.

**Recursion must be bounded and observable.** Define depth, fan-out, concurrency, time and
resource budgets, loop prevention, cancellation and failure behavior. Trace nested
capabilities and their versions, evidence and outcomes so reviewers can explain who
invoked what and under which authority. Composition is not permission for uncontrolled
recursion or an independent scheduler/orchestrator at every level.

Agents **reuse CAOS infrastructure**: established tool specifications, capability
registries, persistence adapters, DI and trusted governance/lifecycle mechanisms. They do
not instantiate independent trusted kernels. This applies
[architectural self-application](#15-architectural-self-application--caos-uses-caos) at the
agent scale, preserving its kernel exception and executable-version qualification.
The cognitive-OS description is an engineering composition model, not a claim that every
such mechanism is implemented, deployed or equivalent to a biological mind.

## Architecture-review checklist

- Before adding an internal process, service, manager or controller: can this responsibility
  be implemented as a registered CAOS tool, and can its coordination be expressed through
  an existing CAOS workflow?
- Does it genuinely belong in the trusted kernel? What invariant requires that placement?
- Are we reusing existing CAOS contracts, infrastructure and lifecycle ownership?
- Can it scale without binding logical identities to physical processes?
- Are identity, tenant, authority, delegation and resource-access boundaries preserved?
- For recursive agent composition, are persistent identity and UTU enforcement preserved
  at every boundary, with observable resource/depth limits and no duplicated trusted kernel?
- Is the proposed pattern solving a real problem, with simpler alternatives considered?
- Are optimizations supported by evidence and bounded acceptance criteria?
- Does this help deliver the next working vertical slice, currently `#weather`?
- What happens on failure, interruption, duplication, ambiguous completion or retry?

Record the responsibility, evidence, trade-off and acceptance tests in the owning design
or ADR when needed. This checklist guides human/contributor review; it creates no runtime
policy engine, new authorization grant or automatic architectural approval.

## Relationship to existing guidance

- [Architecture index](../README.md): canonical source/test navigation and evidence discipline.
- [Root README](../../../README.md#engineering-principles) and [AGENTS.md](../../../AGENTS.md):
  vision summary and contributor procedures, not duplicate principle catalogs.
- [ToolSpec skill](../../../.agents/skills/aosa-tool-spec/SKILL.md) and
  [NCSC/CPBX skill](../../../.agents/skills/ncsc-cpbx/SKILL.md): scoped engineering procedures.
- [OPS V2 contracts](../../../src/base/operations/v2/README.md): specific implemented
  persistence/fencing boundaries and future runtime requirements; these principles do not
  approve an executor design or claim its deployment.
- [Composable architecture vision](../esoteric/research/CAOS_Composable_Cognitive_Architecture_Vision_2026-10-28.md):
  historical research context, including tool-first motivation and an earlier implementation
  sequence. Preserve that record; the current delivery priority is principle 11, not its
  historical roadmap. Research proposals are not promoted to accepted runtime contracts.
- [ADR-0001](../decisions/ADR-0001-thoth-privileged-knowledge.md) remains **Proposed**;
  [ADR-0002](../decisions/ADR-0002-exoteric-esoteric-knowledge.md) remains **Accepted for
  documentation organization only**. Neither status is changed by this consolidation.

[Exoteric engineering](README.md) and [Esoteric research](../esoteric/README.md) are
complementary perspectives on one system, not authority/access tiers. Philosophy may
motivate a design; it cannot grant permissions, prove implementation or substitute for
engineering evidence. This document does not require accepting a metaphysical premise.
