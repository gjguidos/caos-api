# CAOS

### Cognitive Agentic Operative System

**Cognitive infrastructure for connected, autonomous, and observable systems.**

CAOS is an emerging, event-driven platform for building and operating cognitive agents, enterprise integrations, intelligent tools, and automated workflows. It is designed around **AOSA (Agent-Oriented System Architecture)** and developed under **CAOS Intelligence**.

CAOS aims to make cognition a reusable system capability—not a collection of prompts hidden inside individual applications. Agents, workflows, crawlers, integrations, and tools should be able to collaborate through common execution contracts while remaining observable, governable, and independently useful.

> **Many minds. One coherent system.**
>
> *All is One. One is All.*

## Why CAOS?

Enterprise systems need to connect data, services, and processes. Increasingly, they also need to interpret information, reason about ambiguous inputs, and make bounded decisions.

Traditional integration platforms handle connectivity and automation well. Agent frameworks introduce autonomy and model-driven behavior. CAOS seeks to connect these worlds through one architecture:

- **Integrate** enterprise APIs, databases, documents, websites, and internal services.
- **Orchestrate** deterministic and AI-assisted workflows.
- **Acquire and analyze** information using first-class tools such as a web crawler.
- **Enable cognition** through reusable perception, attention, memory, interpretation, planning, and evaluation capabilities.
- **Operate responsibly** with identity, authorization, lifecycle management, auditability, observability, and governance.

**LLM reasoning is optional, not mandatory.** A workflow may run entirely deterministically, invoke a model for a specific analysis step, or delegate bounded work to a cognitive agent.

## Platform architecture

```text
                         CAOS Intelligence
                                │
                   CAOS — Cognitive Agentic
                         Operative System
                                │
            ┌───────────────────┴───────────────────┐
            │          Platform Capabilities        │
            │                                       │
            │  Workflow Engine    Web Crawler       │
            │  Client Tools       Cognitive Agents  │
            │  Custom Integrations & Applications   │
            └───────────────────┬───────────────────┘
                                │
            ┌───────────────────┴───────────────────┐
            │             AOSA / CAOS Core          │
            │                                       │
            │  Event Engine       Agent Runtime     │
            │  Cognitive Services & Workspace       │
            │  Capability Registry & Execution      │
            │  Identity, Permissions & Governance    │
            │  Operations, Tracing & Observability  │
            └───────────────────┬───────────────────┘
                                │
            ┌───────────────────┴───────────────────┐
            │        Systems and Resources          │
            │                                       │
            │  APIs · Databases · Files · Websites  │
            │  LLMs · Multimodal Models · Services  │
            └───────────────────────────────────────┘
```

This is the **target architecture**. Specific services and interfaces may evolve as the platform is implemented.

### AOSA: the architectural foundation

**Agent-Oriented System Architecture (AOSA)** provides the organizing principles for agent identity, lifecycle, capabilities, events, communication, and controlled execution.

CAOS builds on these principles to provide a shared operating environment for cognitive and non-cognitive components. **AOSA is the architecture; CAOS is the platform built around it.**

## First-class capabilities

### Workflow Engine

The workflow capability coordinates multi-step business processes across internal and external systems.

Target responsibilities include:

- Deterministic and event-triggered workflows.
- Tool, API, crawler, and agent invocation.
- Conditional branches, dependencies, retries, and cancellation.
- Optional cognitive reasoning and human approval steps.
- Execution history, structured outputs, and operational tracing.

A workflow is a first-class operational entity—not merely a sequence of LLM tool calls.

### Web Crawler

The web crawler is a first-class acquisition and analysis capability intended for large-scale, bounded web discovery and processing.

Its scope includes:

- Sitemap discovery and URL inventory management.
- Controlled acquisition, parsing, and reporting.
- Document, media, and structured content extraction.
- Queue-backed processing and durable execution state where appropriate.
- Integration with workflows, agents, and downstream client systems.

The crawler can operate without an LLM; reasoning or analysis can be introduced downstream when useful.

### Client tools and integrations

CAOS Intelligence develops integrations and tools for client-specific systems and processes. A tool may expose an API operation, transform data, retrieve knowledge, perform analysis, or coordinate a more complex execution.

Tools should use common capability contracts so they can participate in workflows and agentic operations without sacrificing consistent authorization, traceability, or lifecycle behavior.

### Cognitive agents

Agents are configurable cognitive entities with identities, capabilities, resources, permissions, and context. Their personalities and cognitive profiles influence **how they interpret and reason**, not merely how they phrase responses.

An agent can act independently within delegated authority, call tools, collaborate with other agents, or expose its own capabilities for use by a wider system.

**A tool may be implemented by an agent, but not every tool needs to be an agent.** Pure, cognitive, and agentic execution should remain possible behind compatible interfaces.

## Cognitive architecture

The intended CAOS cognitive core separates **coordination**, **cognitive state**, and **cognitive operations**.

| Component | Purpose |
| --- | --- |
| Cognitive Event Engine | Dispatch typed events, correlate operations, and enforce execution boundaries. |
| Cognitive Workspace | Preserve active goals, intentions, observations, and working context. |
| Context Service | Assemble agent identity, personality, relevant history, and authorized knowledge. |
| Perception and Attention | Normalize incoming signals and determine what warrants processing. |
| Memory | Retrieve and preserve relevant experiences subject to access and retention policies. |
| Interpretation and Planning | Understand requests, form intentions, and determine next steps. |
| Evaluation | Compare outcomes with expectations and support refinement. |

Services implement capabilities; **handlers respond to events and coordinate services**. The event engine provides transport and dispatch, not cognition by itself.

### Events as a common coordination mechanism

The target event model enables operations across agents, workflows, crawlers, tools, and cognitive services to share a coherent lifecycle.

Example event names (proposed contracts, not a finalized API):

```text
workflow.started
crawler.acquisition.completed
cognition.interpret.requested
cognition.interpret.completed
capability.execute.requested
capability.execute.completed
operation.failed
```

Events should support correlation and causation identifiers, operation and tenant context, actor identity, schema versioning, and policy-aware delivery. Authorization should be checked at execution boundaries; merely knowing an event name must not grant a capability.

### Cognitive recursivity

CAOS is guided by the principle that **a cognitive entity can be both an autonomous whole and a contributing part of a larger cognitive system**.

- An individual agent has its own identity, memory, intentions, and cognitive processes.
- Multiple agents can contribute to a coordinated workflow or collective cognitive process.
- A sophisticated tool may itself be backed by a cognitive agent.
- Shared contracts enable composition without merging identities, private memory, or decision authority.

This is CAOS's architectural interpretation of **“All is One. One is All.”** Unity means interoperability and coherence, not unrestricted access or loss of autonomy.

## Governance and observability by design

CAOS aims to provide consistent operational guarantees across deterministic and cognitive execution.

Every executable capability should expose a traceable chain:

**Identity → Authorization → Execution → Events → Observation → Outcome**

The core should make it possible to answer:

- Who or what initiated an operation?
- Which permissions allowed it?
- Which systems and resources were accessed?
- Where was LLM reasoning used, and what was its role?
- What actions were taken, and which artifacts were produced?
- What failed, retried, timed out, or required human intervention?

The target design includes tenant isolation, delegated authority, audit events, secret handling, bounded execution, cancellation, and policy checks. Existing infrastructure such as OpenTelemetry, Prometheus, and Grafana can support telemetry; CAOS adds domain-specific meaning to cognitive, tool, and workflow traces.

**Observability is not the same as storing every prompt or sensitive payload.** Data minimization, retention, redaction, and access controls must be part of the design.

## Example: intelligent content operations

A client wants to synchronize product information, inspect changes on public websites, and selectively use AI for analysis:

```text
Product update received
        │
        ▼
Workflow initiated
        │
        ├── Retrieve ERP / PIM data
        ├── Run Web Crawler
        ├── Compare acquired content
        ├── Request cognitive analysis (optional)
        ├── Apply validation and approval policy
        └── Publish approved changes
        │
        ▼
Structured outcome + execution trace
```

The same platform coordinates acquisition, deterministic processing, model-assisted analysis, approval, and external system updates under common governance.

## JOSHUA and cognitive collaborators

**JOSHUA** is the envisioned executive cognitive agent and coordinating interface within the CAOS ecosystem. JOSHUA should be able to delegate, observe, integrate, and intervene according to explicit authority—without becoming a mandatory bottleneck for every operation.

Specialized cognitive agents, including the proposed **Anunnaki cognition bearers**, may later contribute particular forms of interpretation, creativity, reflection, and evaluation. They are **future compositional roles**, not prerequisites for the core runtime.

## Engineering principles

The canonical architecture-review guidance is [AOSA Engineering Principles](docs/architecture/exoteric/AOSA_ENGINEERING_PRINCIPLES.md). In brief:

- **CAOS uses CAOS:** prefer its governed, reusable capabilities for internal work, with a minimal trusted kernel and optional reasoning; see [architectural self-application](docs/architecture/exoteric/AOSA_ENGINEERING_PRINCIPLES.md#15-architectural-self-application--caos-uses-caos).
- **Agents are cognitive operating environments:** persistent identities consume and expose registered capabilities through bounded, observable composition, reusing CAOS governance rather than separate trusted kernels; LLM reasoning remains optional. See [cognitive OS recursivity](docs/architecture/exoteric/AOSA_ENGINEERING_PRINCIPLES.md#16-cognitive-os-recursivity--agents-as-cognitive-operating-environments).
- Keep capability contracts independent of transport, provider and deployment topology.
- Preserve explicit identity, tenant isolation, authorization and independent lifecycles.
- Design for horizontal scale and bounded resources; optimize from measurements.
- Deliver working vertical slices with sourced knowledge, traceability and explicit failure/effect guarantees.

## Development direction

CAOS is an evolving platform vision. The following are **proposed implementation themes**, not a prerequisite pipeline or a claim that all components are available today. Sequence work through the [vertical-slice delivery principle](docs/architecture/exoteric/AOSA_ENGINEERING_PRINCIPLES.md#11-vertical-slice-delivery-toward-working-capabilities).

| Theme | Focus |
| --- | --- |
| 1. Common contracts | Event envelopes, operation lifecycle, capability schemas, identity and permission boundaries. |
| 2. First-class tool integration | Bring workflow and crawler execution under shared contracts without regressing existing behavior. |
| 3. Cognitive foundation | Cognitive workspace, context resolution, memory interfaces, and initial cognitive handlers. |
| 4. Agentic composition | Agent-to-agent delegation, agent-backed tools, and capability routing. |
| 5. Enterprise platform | Client integration SDKs, governance interfaces, observability views, and deployment options. |

The immediate delivery priority is the first working **`#weather` channel**: persistent channel context, Adam and specialist agents, shared sourced knowledge, and governed capability interaction. This is a target, not a statement that the slice is already implemented. Enlil and other infrastructure should provide its smallest necessary foundations. **Workflow Engine** and **Web Crawler** remain representative validation workloads where they support that slice, not mandatory first milestones.

## Getting started

> **Repository setup instructions are pending.** This README describes the platform's intended architecture and product direction; it does not assume a finalized CAOS repository layout, API, installation command, or deployment procedure.

As implementation stabilizes, this section will document:

- Local prerequisites and environment configuration.
- How to start the CAOS runtime and dependencies.
- How to register a capability or event handler.
- How to define and execute a workflow.
- How to run a crawler operation and inspect its outputs.
- How to configure agents, cognitive services, and observability.

## Project and organization

- **Organization:** CAOS Intelligence
- **Platform:** CAOS — Cognitive Agentic Operative System
- **Architectural foundation:** AOSA — Agent-Oriented System Architecture
- **Executive cognitive agent:** JOSHUA

**CAOS Intelligence builds cognitive infrastructure for connected, governable, agentic systems.**

## License

To be determined by the project maintainers.
