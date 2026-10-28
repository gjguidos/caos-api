# AOSA Research Index

**Scope:** CAOS-authored research questions, architectural hypotheses and proposed experiments. These documents are not accepted runtime contracts or evidence that experiments have run. They extend the [Esoteric perspective](../README.md) on the same CAOS system described by the [Exoteric documentation](../../exoteric/README.md).

## Research directions and source availability

| Research direction | Source | Status and evidence limits |
| --- | --- | --- |
| Social Cognition / Emergent Civilizations | [AOSA research document](AOSA_Social_Cognition_and_Emergent_Civilizations.md) | **Research proposal / not implemented.** Directed relationship histories, context-dependent behavior, norm formation and candidate experiments; not a deployed social graph, civilization engine or validated psychological model |
| Context-Adaptive EQ / Emotional Intelligence | **`CONTEXT_ADAPTIVE_EQ.md` source pending; no document link yet** | Referenced as related work and discussed in the Social Cognition document's [terms](AOSA_Social_Cognition_and_Emergent_Civilizations.md#2-terms-and-conceptual-separation) and [EQ interaction hypothesis](AOSA_Social_Cognition_and_Emergent_Civilizations.md#5-how-eq-can-change-an-edge). These references do not establish the missing document's content, authorship, acceptance or implementation |

The Context-Adaptive EQ source was not found during this reconciliation and has not been reconstructed from related prose. Supply the original or an approved path before adding a standalone source entry; keep it proposed unless separately reviewed. The same source limitation applies to the previously reported `EXTENDED_README.md`: the existing [Esoteric README](../README.md) is the navigation entry point, not a claim to have incorporated that missing text.

## Research and security boundaries

- Separate philosophical motivation, CAOS-authored hypotheses, proposed experiments and executed results. The experiment identifiers in a proposal do not imply test implementations or measured outcomes.
- Social relationship graphs, capability bindings/trusted lines and authorization are distinct. Familiarity, inferred empathy, shared norms or a RulePack cannot grant UTU rights or bypass NCSC/CPBX.
- Proposed relational memory/appraisal must retain provenance, privacy, tenant/resource scope and correction/deletion safeguards; this index introduces no storage or policy mechanism.
- If future evidence supports an engineering mechanism, record it in a separately reviewed ADR with implementation and acceptance criteria. Do not silently promote research to an operational guarantee.

## Related knowledge

- [CAOS Hermeticum, Volume I](../CAOS_Hermeticum_Volume_I.md) — founding philosophical framing; [original publication PDF](../CAOS_Hermeticum_Volume_I_The_Foundations.pdf) remains a separate preserved artifact.
- [Exoteric/Esoteric documentation decision](../../decisions/ADR-0002-exoteric-esoteric-knowledge.md) — accepted organization, not acceptance of these hypotheses.
- [Architecture evidence index](../../README.md) — technical source and test entry points; a reference is not a new test run.
- Use repository-root `.agents/skills/caos-architect/SKILL.md` to explain this research and `.agents/skills/caos-docs/SKILL.md` to maintain its documentation. Neither depends on THOTH being provisioned.
