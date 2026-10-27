# ADR-0002 — Exoteric and Esoteric Knowledge Perspectives

**Status:** Accepted for documentation organization only

**Date:** 2026-10-27

**Scope:** CAOS architecture knowledge, contributor guidance and proposed THOTH explanations

## Context

CAOS needs practical, testable enterprise explanations as well as a place to examine cognitive models, Hermetic influences, creation mythologies and conceptual motivations. Mixing these without provenance risks presenting philosophy as verified software behavior; omitting either loses part of the architectural rationale.

## Decision

- Maintain [Exoteric](../exoteric/README.md) and [Esoteric](../esoteric/README.md) entry points for **the same CAOS system**, linked through the [knowledge index](../README.md). They are complementary perspectives, not separate architectures, runtimes, products or truth hierarchies.
- Exoteric documentation describes enterprise operating-system concepts, contracts, observable behavior, security and implementation status. Esoteric documentation records research philosophy, symbolic models and hypotheses. Neither perspective implies a public/private classification or authorization level.
- Connect an inspirational idea to a concrete mechanism only with an explicit engineering rationale, source/status reference and verifiable behavior or proposed acceptance criterion. An analogy is not proof of equivalence or proof that the mechanism was historically derived from that source.
- Distinguish **ancient source material**, **modern translation/interpretation**, **CAOS-authored philosophical hypotheses** and **technical claims with repository/test evidence**. Retain the knowledge index's implemented/proposed/unknown distinctions. Cite work/edition/section for historical claims and source/wiring/test evidence for technical claims.
- Symbolic inspiration cannot create privileges, override deterministic policy or substitute for testing. No metaphysical premise is required to use the enterprise system, and no test result proves a metaphysical proposition.
- OpenCode's existing `caos-architect` skill and research agent explain these perspectives using the same evidence discipline. [THOTH's modes, tools and provisioning](ADR-0001-thoth-privileged-knowledge.md) remain proposed and subject to the existing authorization architecture.

## Consequences

Keep the entry points concise and expandable; link to existing subsystem docs and specialist skills instead of copying contracts. Add future research notes under the appropriate perspective with sources and status. Do not invent a parallel registry, ontology/policy runtime, ingestion process or authorization scheme for this taxonomy.

Acceptance here records the documentation principle established for CAOS. It does **not** accept the separate THOTH implementation proposal, validate cognitive homeostasis, establish consciousness or implement OPS V2/WFT V2. Runtime changes require their own review, code, tests and authorization.
