# AGENTS.md

Repository-specific instructions for AI coding agents working on CAOS. Keep this file concise, factual, and focused on things an agent might otherwise get wrong.

## What this is

`caos-api` is the Python/FastAPI backend of **CAOS — Cognitive Agentic Operative System**, developed under CAOS Intelligence.

**AOSA (Agent-Oriented System Architecture)** is the architectural foundation. CAOS is the platform built around it.

CAOS is evolving toward an event-driven system in which agents, tools, workflows, crawlers, and cognitive services communicate through common execution contracts with explicit authorization, governance, and observability.

Read `README.md` for the platform vision. **Do not assume that every component described in the README has been implemented.**

## Technical environment

- Python 3.11+
- FastAPI
- Dependency Injector
- Pydantic Settings
- MongoDB
- Redis
- RabbitMQ / aio-pika
- Docker Compose
- uv for Python dependencies
- pytest, Ruff, and mypy

The application is developed primarily inside Docker.

Start the environment:

```bash
docker compose up -d --build
```

Run commands inside the API container:

```bash
docker compose exec api python -m pytest
docker compose exec api python -m pytest path/to/test_file.py
```

Use the existing container and development tooling. Do not introduce additional package managers or infrastructure without a reason.

## Architecture

### Dependency injection

The root container is:

`src/base/dependencies/di_container.py`

Subcontainers live in:

`src/base/dependencies/containers/`

- Register infrastructure dependencies in the appropriate container.
- Reuse existing singleton providers where appropriate.
- FastAPI dependencies use `@inject` and `Depends(Provide[...])`.
- Ensure injected modules are correctly wired.
- Avoid constructing infrastructure clients directly inside API routes.

### Event-driven architecture

CAOS introduces an event-driven cognitive runtime alongside inherited AOSA/AgentVerse components.

Conceptual separation:

- **Event transport:** RabbitMQ and messaging infrastructure.
- **Event runtime:** event registration, routing, execution boundaries, and authorization.
- **Cognitive services:** memory, interpretation, planning, context, and related capabilities.
- **Agents:** identities with authorized capabilities, context, and execution behavior.
- **Tools and workflows:** executable capabilities that may be deterministic or cognitive.

Important principles:

- Events coordinate; services implement behavior.
- An event name does not grant execution permission.
- Agents must receive authorized interfaces rather than unrestricted infrastructure access.
- Tools may use agents, but not every tool requires an agent.
- LLM reasoning is optional.
- Preserve correlation, traceability, and operation identity across execution boundaries.

### Domain ownership and AOSA registries

- Keep `src/base` generic and independent of AgentVerse; place NCSC PBX and its synaptic bindings under `src/domains/agentverse/`.
- Before creating a registry, inspect `src/domains/agentverse/registries/base.py`, the relevant specialized registry, `registries/registries.py`, and `registries/utils/register_registry.py`.
- Prefer the established `Registry[T]` conventions (decorator registration, metadata, lookup, catalog and initialization), without forcing inappropriate key or replacement semantics onto a new registry.
- Generic registry keys are canonicalized. NCSC signals such as `personality.infuse`, `context.delta.build`, and `prompt.build` are **exact dotted addresses**; preserve them without collisions.
- The generic registry supports last-write-wins registration for module reloads. Security-sensitive synaptic bindings must reject conflicting providers and use a controlled registration lifecycle.
- Distinguish registries by ownership: AOSA component registries describe agents/tools/workflows; `CognitiveEventRegistry` defines broker event contracts and subscriptions; `SynapticRegistry` binds addressable capability handlers. Do not duplicate event contracts or message brokers.

### NCSC PBX evolution (design target, not assumed implemented)

NCSC (Neural Cognitive Synapse Center) PBX is the AgentVerse-domain communication and capability exchange built **on** the existing `src/base/events/v2/` event infrastructure. That infrastructure already defines event contracts, policy, authorized clients, signed envelopes and broker runtime; preserve it.

- NCSC PBX resolves, authorizes, routes and correlates calls; it does **not** decide cognitive recipes or replace a workflow engine.
- Use declarative capability bindings/decorators and thin adapters; avoid a new message bus, duplicate ToolManager or mandatory UniversalOrchestrator.
- Prefer authorized local calls for in-process deterministic operations; use RabbitMQ for appropriate distributed events. Both paths must enforce equivalent security boundaries.
- Keep trusted caller identity, tenant, scope, target and correlation/causation across nested calls. Registration or an event name is **not** a permission grant. Protect against recursive loops, cross-tenant routing and privilege escalation.
- Treat `personality.infuse`, `context.delta.build`, `prompt.build`, and `agent.action.chat` as **proposed capability identifiers**, not implemented APIs unless verified in code.
- Tools may expose cognitive or deterministic operations. Existing LLM-oriented tools are context providers; preserve context merging, personality/role/rule-pack composition and model execution semantics while migrating.
- An agent's approved tool-event grants are distinct from the tool provider's configuration and availability. Do not trust client-supplied grants or instantiate every tool inside an agent by default in new designs.

### Agent construction and V2 migration

- Preserve the AOSA composition model: **type → role → personality → rule-pack → authorized tool events**.
- `AgentService` handles persisted definitions/access and runtime retrieval; `AgentFactory` builds configured agent instances. Inspect their actual interfaces before proposing `AgentFactoryV2`.
- New `ChatAgentV2` or factory V2 paths may coexist with V1. Reuse construction, registries, DI, configuration and cognitive resources rather than copy existing logic.
- Existing `ChatAgent.respond()` and the LLM invoker depend on the legacy UniversalOrchestrator/context execution path. Wrapping it in PBX does not remove that dependency.
- Preserve WebSocket authentication, per-session runtime ownership, lifecycle, turn serialization and existing client syntax such as `@write --topic=cat` while migrating behind adapters.
- Separate external WebSocket message routing, PBX capability dispatch, agent construction and cognitive execution; do not turn NCSC into another centralized orchestrator.
- JOSHUA is the encompassing operative entity in the CAOS design, not a required `BaseAgent` subclass. Individual AOSA agents are composed and activated inside that environment. Mythological governors/Genesis concepts should remain domain-level composition logic, not hardcoded base infrastructure.

### Existing and new systems

CAOS is being developed using infrastructure and architectural experience from AgentVerse/IAOS.

Do not assume every inherited component belongs to the finalized CAOS architecture.

- Preserve existing working behavior unless the task explicitly requests migration.
- Keep new cognitive/event contracts clearly separated from legacy implementation details.
- Avoid broad refactoring when implementing new CAOS capabilities.
- Verify actual code paths before describing a component as implemented.

### Configuration and resources

Application settings are aggregated in:

`src/base/config/config.py`

Model repositories, database clients, messaging services, and other infrastructure are provided through DI.

Reuse the configured repository catalog and existing infrastructure services. Avoid building duplicate client instances.

## Testing and code quality

Tests live alongside the relevant source code under `src/**/tests/`.

Python code must remain compatible with Python 3.11.

- Use type annotations.
- Prefer existing project conventions.
- Use `src.`-prefixed imports in application code where appropriate.
- Keep changes scoped to the requested files.
- Do not run repository-wide auto-fixes without reviewing their impact.

Ruff is configured with automatic fixes. Prefer targeted linting:

```bash
ruff check path/to/changed_code
ruff format --check path/to/changed_code
```

Run relevant tests after changes. Report tests that could not be run.

Do not assume CI currently validates every Python test and lint rule; inspect the workflows.

## Release management

CAOS uses Git-driven semantic versioning.

Branch lifecycle:

```text
feature/* → alpha → Pull Request → master
```

- Feature branches do not publish releases.
- `alpha` produces prereleases through semantic-release.
- Stable promotion requires a reviewed PR from `alpha` to `master`.
- `master` produces stable releases.
- Git tags are the source of truth for published versions.
- Docker images are published to GHCR for AMD64 and ARM64.
- setuptools-scm derives Python package versions.
- VersionService exposes application build metadata.

Use Conventional Commits.

Examples:

```text
feat(events): introduce cognitive event runtime
fix(messaging): correct RabbitMQ connection handling
perf(crawler): reduce report persistence overhead
chore(release): update release workflow
```

Consult:

- `docs/RELEASE_WORKFLOW.md`
- `.agents/skills/caos-release/SKILL.md`

Never independently publish a release, merge a promotion PR, force-push a release branch, rewrite published history, or move release tags without explicit authorization.

## Agent behavior

When working in this repository:

1. Inspect the relevant implementation before proposing changes.
2. Distinguish confirmed behavior from proposed architecture.
3. Explain dependency and execution flows when making structural changes.
4. Prefer small, verifiable changes.
5. Preserve existing contracts unless migration is explicitly requested.
6. Never bypass authorization or governance mechanisms to simplify an implementation.
7. Report potential regressions, unverified assumptions, and tests not executed.

For codebase investigation, use the CAOS research agent.

For release preparation, verification, or promotion, use the CAOS release skill.

For registry changes, use `.agents/skills/aosa-registry/SKILL.md` and inspect the existing registry implementations and tests first.

### Architecture knowledge discovery

For conceptual, architectural, security and best-practice questions, use `.agents/skills/caos-architect/SKILL.md` and `docs/architecture/README.md`. Keep using the existing `.agents/research.md` CAOS research agent for deeper read-only source investigation and the specialist skills for engineering procedures.

Ground answers in current source, wiring and test evidence; distinguish implemented behavior from documented intent and proposals. THOTH is a planned privileged knowledge citizen, not an installed runtime or a grant of repository access. This knowledge foundation introduces no runtime tools, registry or authorization mechanism.

CAOS has complementary **Exoteric** (`docs/architecture/exoteric/README.md`) enterprise/engineering and **Esoteric** (`docs/architecture/esoteric/README.md`) research/philosophical perspectives on the same system, not separate implementations or access tiers. Distinguish ancient sources, modern interpretations, CAOS hypotheses and technical evidence. Symbolic archetypes and THOTH's proposed cognitive modes never establish authority; existing authorization boundaries still govern access.

## Important references

- `README.md` — CAOS vision and architecture
- `src/base/dependencies/di_container.py` — root DI
- `src/base/dependencies/containers/` — infrastructure providers
- `src/base/system/initialize_app.py` — application initialization
- `src/base/lifespan/` — application resource lifecycle
- `src/base/config/` — settings
- `src/domains/agentverse/registries/` — AOSA registry conventions and instances
- `src/base/events/v2/` — existing event contracts, policy, registry and runtime
- `docs/RELEASE_WORKFLOW.md` — release process
- `.github/workflows/release.yml` — release automation
- `docs/architecture/README.md` — architecture knowledge index and evidence entry points
- `docs/architecture/decisions/ADR-0001-thoth-privileged-knowledge.md` — proposed THOTH boundaries

Always inspect these files before relying on assumptions about their current implementation.
