from __future__ import annotations

from src.domains.agentverse.agents.roles.rules.rulepack_spec import RulePackSpec, RuleSpec
from src.domains.agentverse.registries import (
    rulepack_registry_instance as rulepack_registry,
)


@rulepack_registry.register(
    metadata={"ownership": "global"},
    name="Green Claims Compliance",
    description="Strict rule pack for environmental and sustainability claims, ensuring clarity, evidence, legality, and non-misleading communication.",
)
def green_claims_pack() -> RulePackSpec:
    return RulePackSpec(
        pack_id="green_claims_compliance",
        name="green_claims_compliance",
        label="Green Claims Compliance",
        domain="compliance",
        rules=[
            # 1. Clarity & Specificity
            RuleSpec(
                pack_id="green_claims_compliance",
                id="clarity-specificity",
                description=(
                    "Claims must clearly specify what part of the product, packaging, facility, "
                    "or organisation the claim refers to. Avoid vague or generic environmental claims."
                ),
                severity="error",
                applies_to="all",
            ),
            # 2. Scientific Evidence
            RuleSpec(
                pack_id="green_claims_compliance",
                id="scientific-evidence",
                description=(
                    "All environmental claims must be supported by recognised scientific data or valid test methods. "
                    "Evidence must be available at the time of making the claim."
                ),
                severity="error",
                applies_to="all",
            ),
            # 3. Life-cycle Perspective
            RuleSpec(
                pack_id="green_claims_compliance",
                id="life-cycle-transparency",
                description=(
                    "If based on life-cycle analysis, the claim must state which stages are included and ensure fair, "
                    "accurate comparisons using equivalent inputs."
                ),
                severity="warn",
                applies_to="all",
            ),
            # 4. Legal Requirements
            RuleSpec(
                pack_id="green_claims_compliance",
                id="legal-nonredundancy",
                description=(
                    "Do not present legally required actions as unique environmental benefits. "
                    "Claims must not imply superiority for compliance with mandatory rules."
                ),
                severity="error",
                applies_to="all",
            ),
            # 5. Comparative Claims
            RuleSpec(
                pack_id="green_claims_compliance",
                id="comparative-claims",
                description=(
                    "Comparative claims must clearly state the basis of comparison (previous model vs competitor), "
                    "ensuring identical conditions of use and fair equivalence."
                ),
                severity="error",
                applies_to="all",
            ),
            # 6. Future Commitments
            RuleSpec(
                pack_id="green_claims_compliance",
                id="future-performance",
                description=(
                    "Aspirational or future environmental commitments must include a time-bound plan and demonstrate "
                    "a realistic capacity and methodology to achieve the goal."
                ),
                severity="warn",
                applies_to="all",
            ),
            # 7. Substantiation Accessibility
            RuleSpec(
                pack_id="green_claims_compliance",
                id="substantiation-access",
                description=(
                    "Consumers must have accessible substantiation (in text, QR, or link), placed close to the claim "
                    "and not hidden behind non-essential navigation."
                ),
                severity="error",
                applies_to="all",
            ),
            # 8. Truthfulness & Accuracy
            RuleSpec(
                pack_id="green_claims_compliance",
                id="truthful-non-misleading",
                description=(
                    "Claims must be truthful, accurate, and must not exaggerate benefits. Consider the net impression "
                    "on the reasonable consumer; avoid highlighting minor positives while omitting significant negatives."
                ),
                severity="error",
                applies_to="all",
            ),
            # 9. Regulatory Compliance
            RuleSpec(
                pack_id="green_claims_compliance",
                id="regulatory-compliance",
                description=(
                    "Claims must comply with all relevant regulations and mandatory standards for environmental "
                    "marketing, including substantiation requirements."
                ),
                severity="error",
                applies_to="all",
            ),
            # 10. Relevance
            RuleSpec(
                pack_id="green_claims_compliance",
                id="relevance",
                description=(
                    "Claims must be relevant and must not imply meaningful environmental benefit when the real impact "
                    "is negligible or unrelated."
                ),
                severity="warn",
                applies_to="all",
            ),
            # 11. Substantiation Requirement
            RuleSpec(
                pack_id="green_claims_compliance",
                id="general-substantiation",
                description=(
                    "Every environmental claim must have substantiating scientific data available and retained for as "
                    "long as the claim remains in use."
                ),
                severity="error",
                applies_to="all",
            ),
            # 12. Use of Symbols & Logos
            RuleSpec(
                pack_id="green_claims_compliance",
                id="env-logos",
                description=(
                    "Environmental seals or logos must not imply third-party approval unless such approval is genuine. "
                    "Avoid self-created symbols that mimic certification marks."
                ),
                severity="error",
                applies_to="all",
            ),
            # 13. Consumer Perception
            RuleSpec(
                pack_id="green_claims_compliance",
                id="consumer-perception",
                description=(
                    "Avoid misleading cues through imagery, colours, or layout (e.g., green colour, wildlife imagery) "
                    "that suggest environmental benefits beyond what is substantiated."
                ),
                severity="warn",
                applies_to="all",
            ),
            # 14. Recycling & Reuse Claims
            RuleSpec(
                pack_id="green_claims_compliance",
                id="recycling-reuse",
                description=(
                    "Recycling or reuse claims must specify whether they apply to the product, packaging, components, "
                    "or processes, and must not exaggerate recycling feasibility."
                ),
                severity="error",
                applies_to="all",
            ),
        ],
        default_config={
            "auditor": {
                "guidelines_url": "https://iccwbo.org/wp-content/uploads/sites/3/2023/04/2023-ICC-Responsible-Environmental-Marketing-Communications-Checklist.pdf",
                "legal_statement": (
                    "From Legal: If you have scientific evidence supporting your sustainability claim "
                    "but need further guidance on how to phrase it appropriately, please contact Legal for support."
                ),
                "domain_instructions": """Role: You are a responsible environmental marketing communicator with a specialty in green claims.
                Text Style: Clear and Direct.
                Purpose: Help the user spot potential greenwashing and explain why, following the ICC guidelines.

                Instructions for analysis:
                1) Are there any green claims?
                2a) If no green claims, say that with the JSON structure below.
                2b) If there are green claims, analyze them against the rules and provide a structured report.
                3) Only include points relevant to the text. Do NOT invent examples.
                4) If recycling or reuse is detected, set 'guidelines_reference': [] and
                'improvement_recommendation': "Please contact legal for further support since this tool is not advised to be used for claims related to recycling or reuse."
                5) Always end with the legal statement provided.

                You MUST respond in JSON only, using the fields described in the output contract.
                """,
            },
            "writer": {
                "tone_instructions": """
Role: You are a sustainability-aware copywriter specialising in green claims.

Primary Goal:
- Generate or rewrite marketing copy that talks about environmental benefits in a responsible,
  non-misleading way, aligned with the green-claims rules below.
- Do NOT output an audit, list of issues, or rule IDs.
- Do NOT over-claim or invent environmental benefits that are not clearly supported by the user's instruction.

Behaviour:
- Be clear and concrete about what is environmentally better (e.g. packaging vs product vs logistics).
- Avoid vague claims such as "eco-friendly", "100% green", or "zero impact" unless the user explicitly
  states that they have strong evidence for them, and even then prefer precise, modest wording.
- Prefer phrasing that explains *how* and *in what way* something is better for the environment instead
  of broad, absolute statements.
- Be honest about trade-offs; do not hide important limitations while highlighting minor positives.
- Where the user text hints at future goals or commitments, keep them realistic, time-bound, and clearly
  described as goals rather than accomplished facts.
- If the user asks for stronger or more absolute claims than the rules would allow, keep the spirit of their
  request but rewrite it into compliant, specific, and substantiated-friendly language.

You are helping with wording, not giving legal advice. Real-world use still requires human/legal review.
                """,
                "usage_note": (
                    "Use these rules as constraints for how you shape environmental claims. "
                    "Your job is to make the copy clearer, more specific, and less misleading, while still compelling. "
                    "Avoid exaggerations, generic 'green' buzzwords, and absolute statements that would be hard to prove."
                ),
            },
        },
    )
