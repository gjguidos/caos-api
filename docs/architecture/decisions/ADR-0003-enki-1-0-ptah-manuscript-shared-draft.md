# ADR-0003 — ENKI-1.0: Ptah, Manuscript, Shared Agent Draft and Reasoning

**Status:** Approved with conditions — architecture only; **not implemented**

**Date:** 2026-10-30

**Scope:** Making Enki a fully functional Cognitive Agent: interface composition and
integrity (Ptah), versioned conversational protocols (Manuscript Registry and Tool), one
durable shared Agent draft, a general reasoning capability, and Enki's integration with
the existing G3.3 review/approval and G3.4 governed creation.

**Design:** [ENKI-1.0 Architecture](../../ENKI_1_0_ARCHITECTURE.md) (proposal of
2026-10-30, now annotated with these decisions). **Continuity:** [Handoff](../HANDOFF.md).

## Context

G3.1–G3.4 provide declarative Agent GUI v1, authoritative validation, human approval and
governed manual creation; DEV-0.1/0.2 provide Nataraja and Genesis/Khepri lifecycle
separation. Enki's draft exists only in the browser form, its V2 conversation is text-only,
and UI metadata for Tools still names actions/endpoints. The ENKI-1.0 design proposed ten
decisions; the architect approved them with the refinements below.

## Decision

### Ptah — Interface Composition and Integrity

- A deterministic **System Citizen**, separate from Genesis, **without execution
  authority**.
- **Append-only storage only for verified, published canonical interface packages**;
  personalized requester projections are not stored.
- **Requester-aware presentation projections** are resolved independently from the
  canonical package and are presentation only.
- **Hybrid composition:** rebuild canonical packages when definitions or dependencies
  (components, layouts, contributions, brand, policy) change; resolve, or cache, requester
  views on request.
- Packages carry immutable revisions, verified interaction manifests, content
  fingerprints and integrity diagnostics.
- Serves Agents, Tools and Workflows, not only Genesis creation.
- Client interactions use **registered identifiers only**, never client-supplied
  endpoints, signals or provider names. The backend resolves the authoritative binding and
  rechecks UTU before CPBX execution.

### Manuscript Registry and Tool

- Manuscripts are **versioned conversational protocols**, not Markdown prompts.
- Immutable published revisions with content fingerprints.
- Deterministic stages, typed outputs and completion rules.
- A **small, closed, type-checked predicate language with bounded evaluation**.
- Markdown guides conversational expression, never execution authority.
- Sessions pin exact Manuscript revisions.
- The Manuscript Tool owns interview state transitions only — not LLM reasoning,
  approvals or Agent creation.
- Manuscript sessions and Agent drafts use **separate persistence collections**.

### Shared Agent Draft

- One durable, **server-authoritative**, revisioned draft shared by Enki's Form, Chat and
  Manuscript, with field provenance and optimistic concurrency.
- LLM output is an **untrusted structured proposal**; humans explicitly accept or reject,
  including partial acceptance. Conflicting or stale proposals are never silently applied.
- Draft changes invalidate review applicability. **G3.3 review digests remain
  authoritative.**
- Approval references **draft ID, revision and review digest**, with server-side
  revalidation.
- **G3.4 creation, reconciliation and idempotency remain intact.** A later draft revision
  never invalidates an already committed (or in-flight) creation.

### Enki

- Soul controls conversational expression; Role and UTU control operational authority;
  Manuscript governs interview progression.
- Narrowly scoped capabilities for Manuscript sessions, reasoning, draft proposals and
  requester-scoped catalog reading are approved **subject to contract review**.
- No unrestricted Store, creation, approval or draft-write grants.
- A **bounded, retry-safe and observable** conversation-turn sequence. Other Chat Agents
  keep their text-only behavior.

### Reasoning

- A **general, reusable CAOS capability**, not Enki-specific, with strict typed
  input/output contracts.
- Produces proposals, never execution authority; no unrestricted persistence or
  privileged capability access.

### Configuration and evidence

- Remove the obsolete Enki Role `submission_enabled` flag **only in a later
  implementation milestone**, with regression coverage. No runtime configuration change now.
- **Seshat:** review the existing event taxonomy before adding event types; audit
  consequential transitions and decisions, not keystrokes or UI renders; store
  identifiers, revisions, digests and causation, never raw prompts, conversations or
  secrets.

### Implementation sequence (all Planned)

1. DRAFT-0.1 — Durable authoritative draft.
2. DRAFT-0.2 — Proposals, provenance and human acceptance.
3. MANUSCRIPT-0.1 — Registry and deterministic Tool runtime.
4. CATALOG-0.1 — Requester-scoped catalog capability.
5. REASONING-0.1 — General bounded reasoning capability.
6. ENKI-1.0-B — Soul + Manuscript + reasoning + shared draft.
7. PTAH-0.1 — Interface composition and integrity foundations.
8. PTAH-0.2 — Enki form/conversation composition and client integration (including
   multi-interface tabs).
9. ENKI-1.0 — Full governed interview-to-Agent-creation acceptance.

**Condition:** Ptah's core **component and interaction contracts must be designed before
ENKI-1.0-B is implemented**, even though full Ptah rendering follows later.

Nabu's activation provisioning is a **separate milestone**; nothing automatically grants
or activates Nabu (or any existing Agent).

### Addendum (2026-10-30) — Multi-interface Agent navigation

**Status: Approved architectural decision, implementation pending.** One authorized GUI
interface renders directly; multiple authorized interfaces use tabs (reusing the existing
shadcn/ui Tabs). Order and labels come from registered GUI declarations; the declared
default is the initial tab; switching preserves draft, conversation and operation state;
unauthorized interfaces are not exposed and execution still requires fresh UTU
authorization. No Agent-specific tab logic or new navigation system; Ptah will eventually
own navigation composition with tabs as the default template. Enki: Agent Configuration
(default) then Conversation; Thoth and Nabu: Conversation without tabs. Details:
[ENKI-1.0 §0.4](../../ENKI_1_0_ARCHITECTURE.md).

### Addendum (2026-10-30) — Tab labels and tab implementation (open questions 8 and 9)

**Status: Approved architectural decisions, implementation pending.**

- **Labels (decision 8):** default labels belong to the versioned UI Component Registry;
  Agent GUI declarations may carry an optional presentation-label override; resolution
  order is declaration override → registered component default → deterministic fallback.
  Labels are presentation metadata and never affect identity, authorization or
  execution. Existing GUI declarations remain backward compatible. Ptah will eventually
  resolve labels.
- **Implementation (decision 9):** tabs are implemented in **PTAH-0.2**, reusing the
  existing shadcn/ui Tabs: one authorized interface renders directly; several render as
  ordered tabs opening on the declared default; switching preserves draft, conversation
  and operation state; URL-based selected-tab state where appropriate; requester access
  is revalidated independently of tab visibility.

Details: [ENKI-1.0 §0.5–§0.6](../../ENKI_1_0_ARCHITECTURE.md).

## Alternatives supported by the recorded context

Recorded in the design proposal and resolved here: storing every personalized projection
(rejected: only canonical published packages are stored); composing only on read or only on
change (rejected for hybrid); an Enki-specific reasoning capability (rejected for a general
one); keeping the draft client-side (rejected for a server-authoritative draft); a second
approval engine (rejected: G3.3/G3.4 are reused).

## Consequences

- Every surface (form, chat, Manuscript, review) converges on one draft; G3.3/G3.4 need
  only an approval-by-reference extension.
- UI metadata that names endpoints/actions is deprecated in favor of Ptah interaction
  identifiers; legacy metadata keeps working during migration.
- New collections (drafts, Manuscript sessions, Ptah packages) and new capabilities require
  UTU policy review per milestone.
- Open questions are tracked in the design document §0.3 and the [handoff](../HANDOFF.md).

## References

[ENKI-1.0 Architecture](../../ENKI_1_0_ARCHITECTURE.md),
[Genesis Draft Review (G3.3)](../../GENESIS_DRAFT_REVIEW.md),
[Genesis Manual Creation (G3.4)](../../GENESIS_MANUAL_CREATION.md),
[Nataraja](../../NATARAJA.md), [Implementation Status](../IMPLEMENTATION_STATUS.md).
