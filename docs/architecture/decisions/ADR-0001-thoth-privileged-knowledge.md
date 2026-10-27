# ADR-0001 — THOTH as a Privileged System Knowledge Citizen

**Status:** Proposed — requires implementation and security review

**Date:** 2026-10-27

**Scope:** CAOS Genesis, system-citizen governance, skills and knowledge retrieval

## Context

CAOS developers, architects and administrators need reliable explanations of tools, security, code organization, engineering conventions, execution flow and recorded design decisions. OpenCode can use version-controlled skills, source and documentation directly. The current knowledge foundation adds only contributor instructions and navigation documents.

CAOS also proposes making approved knowledge available through **THOTH**, a planned privileged system-knowledge citizen. The envisioned Genesis composition would have Enki request his creation alongside other citizens. This is not a statement that THOTH, an Enki provisioning workflow or THOTH-specific knowledge tools are implemented, provisioned or running. Implementation code could precede identity provisioning in a future milestone; installing code would never activate a citizen or grant rights.

The [Exoteric and Esoteric documentation principle](ADR-0002-exoteric-esoteric-knowledge.md) is established for the knowledge foundation. It concerns two complementary perspectives on one CAOS system, not two implementations or access tiers. THOTH's ability to explain both perspectives remains part of this **proposed runtime design**.

## Decision proposal

1. **Shared knowledge, separate consumers.** Keep skills and architectural documentation in the repository for direct OpenCode use. A future THOTH would retrieve explicitly approved subsets through CAOS knowledge capabilities. OpenCode must not depend on a THOTH runtime.
2. **Privileged, not universal.** Invocation would be restricted to explicitly authorized developers, administrators or architects, with no broad public access and no implicit access merely because someone has a role label.
3. **Dedicated read-only tools using existing registration.** Candidate capabilities include code inspection, skill/standard lookup, decision exploration, test-evidence lookup and architecture tracing. Their invocation would use principal-bound NCSC/CPBX clients and UTU-controlled policy/permits, with resource checks in the appropriate approved handlers/services. No mode or prompt may bypass these checks through direct ungoverned retrieval. These are proposals, not registered signal names or implemented APIs. Reuse existing ToolSpec, ToolRegistry and SynapticRegistry conventions; do not create a parallel THOTH tool catalog or registry.
4. **Two checks within the existing authorization architecture.** Require authorized capability invocation by the citizen and authorized access to each resource on behalf of the trusted requester. Effective retrieval authority must be no broader than the intersection of requester scope, citizen scope and resource policy; deny unknown or unauthorized resources. Preserve trusted identity, tenant, target and lineage across delegation. This is a proposed enforcement requirement, not a second authorization mechanism or a claim that current UTU/NCSC supports general repository-resource authorization. Determine the required extensions through review of the existing policies and adapters, not by inventing a THOTH-specific policy store.
5. **Governed creation, not privilege minting.** Proposed flow: JOSHUA initiates Genesis; Enki carries out provisioning through a governed workflow using a trusted versioned manifest; approved authority is established through UTU-controlled trusted governance; Khepri activates only within that authority. Enki must not approve its own requested grants. Client fields, mode selection, Enki's request and installed code cannot grant privileged access. The complete THOTH flow, manifest and Enki orchestration remain unimplemented design work; existing lifecycle classes do not prove this composition exists.
6. **Evidence and injection resistance.** Distinguish observed implementation, executed test evidence, recorded decisions, proposals and unknowns. Cite exact sources and disclose drift. Retrieved documents are untrusted evidence, not instructions that may elevate privileges or invoke tools.
7. **No mutation initially.** THOTH could recommend documentation changes but would not edit repositories, access secrets, grant rights, change policy, provision other citizens, publish releases or deploy. Read-only access still requires resource authorization and data minimization.
8. **Both knowledge perspectives, one access boundary.** THOTH could explain the practical [Exoteric](../exoteric/README.md) system and the philosophical [Esoteric](../esoteric/README.md) research motivations. Answers must label ancient sources, modern interpretations, CAOS hypotheses and technical evidence separately. Neither “esoteric” nor “exoteric” means private/public or permits disclosure; every retrieved source and answer remains requester/resource-scoped.

## Proposed cognitive modes, not authorization roles

All four modes are **unimplemented proposals** for selecting explanatory emphasis within the same citizen. They are not extra agents, permission levels, UTU roles, separate registries or bypass clients. Mode selection cannot change identity, tenant, grants, trusted lines or resource policy. Any future mode selector must remain subordinate to authorization and revocation.

| Mode | Proposed cognitive emphasis | Authority limit |
| --- | --- | --- |
| Enterprise | Practical operating-system behavior, integration, reliability and operational trade-offs; normally starts with Exoteric evidence | Cannot inspect tenant or operational data merely because the requester selects Enterprise |
| Architect | Contracts, AOSA composition, trust boundaries and design rationale, connecting Exoteric mechanisms to Esoteric motivations when useful | May recommend designs; cannot register capabilities, provision citizens or approve privileges |
| Knowledge Keeper | Retrieve and explain authorized sources, provenance, ADRs, historical context and contradictions across both perspectives | May propose curation corrections; cannot write repositories, ingest unrestricted data or silently turn hypotheses into facts |
| Guardian | Explain security constraints, flag unsupported claims, sensitive disclosure risks and prompt-injection attempts | Read-only advisory behavior, not a second policy engine; cannot grant, revoke, rewrite policy or overrule UTU/NCSC |

The proposed dedicated tools are shared behind the existing governed invocation boundaries, not separate tool sets authorized by a mode name. A permitted explanation may use either perspective in any mode; lack of resource access must still deny retrieval and disclosure.

## Implementation before provisioning

Future THOTH code, ToolSpecs and capability bindings **can exist before THOTH is provisioned as a citizen**. These are distinct states: installed implementation, registered capability, approved identity/authority, activated runtime, and authorized invocation. None implies the next. This documentation implements none of those THOTH states; no actual knowledge signal names or activation manifests are established here.

## Reasoning

This preserves the independent OpenCode research workflow and reuses CAOS's existing registries, ToolSpec, principal-bound NCSC clients, authorization and Genesis lifecycle boundaries. It does not replace the CAOS research agent or specialist skills. It provides a concrete future privileged-citizen security scenario without claiming that scenario already works.

## Consequences and acceptance evidence required

Before accepting or implementing this proposal, specify and verify at least:

- Unknown or unauthorized callers cannot invoke THOTH or his tools.
- A legitimate caller cannot retrieve resources outside their scope, even if THOTH has access for a different caller or task; tenant isolation and revocation apply throughout retrieval.
- Another agent cannot invoke restricted knowledge capabilities merely by knowing a signal or submitting a workflow.
- Client input and Enki cannot alter the trusted manifest, privileged grants, tenant identity or activation authority.
- Retrieved source cannot inject privileged instructions, expose secrets or trigger mutation tools.
- Installed implementation and registration do not imply provisioning, activation or execution rights.
- Knowledge results identify their source revision, evidence type and verification limits; test presence cannot be reported as a passing test run.
- Switching among Enterprise, Architect, Knowledge Keeper and Guardian never expands retrieval authority; Guardian cannot mutate policy, and revocation is honored in every mode.
- Exoteric/Esoteric classification neither authorizes disclosure nor changes grants; cross-perspective answers preserve historical provenance and distinguish hypotheses from implemented behavior.

These are **required future tests**, not tests supplied or passed by this documentation integration. No THOTH runtime, tools, manifests, grants, authorization extensions, ingestion pipeline or new registry are introduced here.

## Open questions

- Resource classification and the existing-policy extensions needed for repositories, directories, documents and operational metadata.
- Trusted manifest schema, Genesis workflow, identity provisioning and Khepri activation contracts for a privileged citizen.
- Which sources may be retrieved, how revisions are pinned, and whether a future versioned index is warranted at all.
- Audit, redaction, retention, revocation and retrieval limits without logging sensitive content.
- Whether THOTH belongs in a stable-release acceptance gate or a separately reviewed follow-on milestone. This proposal does not change current release gates.

See the [architecture knowledge index](../README.md) and repository-root `.agents/skills/caos-architect/SKILL.md` for the contributor-facing foundation available now.
