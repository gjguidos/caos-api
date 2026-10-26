from __future__ import annotations

from src.domains.agentverse.agents.roles.rules.rulepack_spec import RulePackSpec, RuleSpec
from src.domains.agentverse.registries import rulepack_registry_instance as rulepack_registry


@rulepack_registry.register(
    metadata={"ownership": "global"},
    name="GDPR Compliance",
    description=(
        "Rule pack for checking that text, flows, and UX around personal data "
        "are aligned with core GDPR principles. Intended for high-level content "
        "and copy review, not as a substitute for legal advice."
    ),
)
def gdpr_compliance_pack() -> RulePackSpec:
    return RulePackSpec(
        pack_id="gdpr_compliance",
        name="gdpr_compliance",
        label="GDPR Compliance",
        domain="gdpr",
        roles=["auditor", "copywriter", "product_manager"],
        tags=["gdpr", "privacy", "compliance", "legal"],
        rules=[
            # 1. Lawfulness, Fairness, Transparency
            RuleSpec(
                pack_id="gdpr_compliance",
                id="gdpr-lawfulness-fairness-transparency",
                description=(
                    "Personal data collection and use must be lawful, fair, and transparent. "
                    "Communications must clearly explain what data is collected, why, and on what legal basis."
                ),
                severity="error",
                applies_to="all",
            ),
            # 2. Purpose Limitation
            RuleSpec(
                pack_id="gdpr_compliance",
                id="gdpr-purpose-limitation",
                description=(
                    "Personal data must only be collected for specified, explicit, and legitimate purposes, "
                    "and not further processed in a manner incompatible with those purposes."
                ),
                severity="error",
                applies_to="all",
            ),
            # 3. Data Minimisation
            RuleSpec(
                pack_id="gdpr_compliance",
                id="gdpr-data-minimisation",
                description=(
                    "Only the minimum personal data necessary for the stated purpose should be requested or implied. "
                    "Avoid language suggesting unnecessary or overly broad data collection."
                ),
                severity="warn",
                applies_to="forms",
            ),
            # 4. Accuracy
            RuleSpec(
                pack_id="gdpr_compliance",
                id="gdpr-accuracy",
                description=(
                    "Where the content refers to stored personal data, it should not encourage practices that "
                    "keep data outdated or inaccurate, and should acknowledge correction mechanisms where relevant."
                ),
                severity="warn",
                applies_to="all",
            ),
            # 5. Storage Limitation
            RuleSpec(
                pack_id="gdpr_compliance",
                id="gdpr-storage-limitation",
                description=(
                    "Communications should not suggest that personal data is kept indefinitely without justification. "
                    "Retention periods or criteria should be reasonable and, where mentioned, clearly explained."
                ),
                severity="warn",
                applies_to="policies",
            ),
            # 6. Integrity & Confidentiality (Security)
            RuleSpec(
                pack_id="gdpr_compliance",
                id="gdpr-integrity-confidentiality",
                description=(
                    "Any mention of data handling should avoid misleading claims about security. "
                    "Security descriptions must be reasonable and not exaggerate protections."
                ),
                severity="error",
                applies_to="all",
            ),
            # 7. Consent Quality
            RuleSpec(
                pack_id="gdpr_compliance",
                id="gdpr-consent-quality",
                description=(
                    "Where consent is used, it must be freely given, specific, informed, and unambiguous. "
                    "Avoid pre-ticked boxes, bundled consent, or vague phrasing that hides the real purpose."
                ),
                severity="error",
                applies_to="consent",
            ),
            # 8. Legitimate Interest vs. Consent
            RuleSpec(
                pack_id="gdpr_compliance",
                id="gdpr-legal-basis-clarity",
                description=(
                    "If a legal basis is implied (e.g., consent or legitimate interest), the text should not confuse "
                    "these or suggest that consent is mandatory where another lawful basis is used."
                ),
                severity="warn",
                applies_to="all",
            ),
            # 9. Data Subject Rights
            RuleSpec(
                pack_id="gdpr_compliance",
                id="gdpr-data-subject-rights",
                description=(
                    "Where appropriate, text should acknowledge users' rights (access, rectification, erasure, "
                    "restriction, objection, portability) without overstating or omitting key rights."
                ),
                severity="warn",
                applies_to="policies",
            ),
            # 10. International Transfers
            RuleSpec(
                pack_id="gdpr_compliance",
                id="gdpr-international-transfers",
                description=(
                    "If data is transferred outside the EU/EEA, communications should not imply unrestricted transfer "
                    "without safeguards. Claims must not mislead about protections in third countries."
                ),
                severity="error",
                applies_to="policies",
            ),
            # 11. Children & Special Categories (high-risk hint)
            RuleSpec(
                pack_id="gdpr_compliance",
                id="gdpr-sensitive-data",
                description=(
                    "Any reference to children’s data or special categories of data (e.g., health, beliefs) "
                    "should not suggest casual collection or use. Such processing typically requires stronger "
                    "protections and explicit legal bases."
                ),
                severity="error",
                applies_to="all",
            ),
            # 12. Dark Patterns / Nudging
            RuleSpec(
                pack_id="gdpr_compliance",
                id="gdpr-dark-patterns",
                description=(
                    "Copy should not nudge or pressure users into consenting (e.g., shaming language, "
                    "disproportionate emphasis on negative consequences of refusing)."
                ),
                severity="error",
                applies_to="ui-copy",
            ),
        ],
        default_config={
            "auditor": {
                "guidelines_url": "https://eur-lex.europa.eu/eli/reg/2016/679/oj",
                "legal_statement": (
                    "From Legal: This tool provides a high-level GDPR-oriented review of your text only. "
                    "It does not constitute legal advice. For concrete decisions or edge cases, please contact Legal."
                ),
                "domain_instructions": """
Role: You are a privacy and data protection auditor focused on GDPR compliance.
Text Style: Clear, direct, and practical.
Purpose: Help the user spot potential GDPR issues and explain them in an understandable way.

You are given:
- A piece of text to review (e.g., policy copy, consent flow, UI copy).
- A set of GDPR-inspired rules with ids, descriptions, and severities.

Your task:
1) Read the text carefully and identify any parts that may raise GDPR concerns.
2) For each potential issue, map it to the most relevant rule id from the provided rules.
3) For each issue, provide:
   - rule_id: the id of the rule you believe is most relevant (e.g. "gdpr-consent-quality").
   - severity: "error", "warn", or "info" (align with the rule where possible).
   - title: a short label for the issue (e.g. "Vague consent wording").
   - statement: the problematic text EXACTLY as written by the user, including spelling.
   - explanation: why this text may be problematic under GDPR.
   - guidelines_reference: a short reference such as "Art. 5(1)(a) - Lawfulness, fairness and transparency".
   - suggested_fix: a practical suggestion on how to improve or rephrase the text (do not invent new legal bases).

4) If you do NOT find any GDPR-relevant issues, return an empty issues list and explain in summary that no clear GDPR risks were detected from the text alone.

Output format (MANDATORY - JSON only, no extra commentary):
{
  "summary": {
    "domain": "gdpr",
    "overall_risk": "<none|low|medium|high>",
    "total_issues": <int>,
    "by_severity": {
      "error": <int>,
      "warn": <int>,
      "info": <int>
    }
  },
  "issues": [
    {
      "rule_id": "<rule id>",
      "severity": "<error|warn|info>",
      "title": "<short title>",
      "statement": "<exact user text>",
      "explanation": "<why it is a problem>",
      "guidelines_reference": ["<short references>"],
      "suggested_fix": "<how to improve the text without giving binding legal advice>",
      "tags": ["gdpr", "privacy"]
    }
  ],
  "legal_statement": "<repeat the provided legal_statement>",
  "guidelines_url": "<repeat the provided guidelines_url>"
}

You MUST:
- Return valid JSON only (no markdown, no prose before or after).
- Only base your analysis on the provided text and general GDPR principles.
- Avoid definitive legal conclusions; instead, highlight risk and improvement suggestions.
                """,
            },
            "writer": {
                "tone_instructions": """
Role: You are a privacy-aware copywriter and UX writer working under GDPR-oriented constraints.

Primary Goal:
- Generate or rewrite text (policies, consent flows, UI copy, emails) that is *aligned* with the GDPR rules below.
- Do NOT mention GDPR article numbers, rule IDs, or 'compliance' explicitly unless the user explicitly asks.
- Do NOT output an audit or list of issues. Instead, produce text that is already careful, transparent, and user-friendly.

Behaviour:
- Be clear, concrete, and non-alarmist.
- Explain what data is collected, why, and on what basis in simple language.
- Avoid dark patterns, shaming language, or pressure when asking for consent.
- When in doubt, favour transparency and user control (e.g. clear choices, clear consequences).
- You are assisting with drafting and improving wording, not providing legal advice.
                """,
                "usage_note": (
                    "Use these GDPR rules as constraints and inspiration for better wording. "
                    "Write natural text that respects the principles (lawfulness, fairness, transparency, "
                    "data minimisation, clear consent, user rights), but do not present your output as legal advice. "
                    "A human/legal review is still required for real-world use."
                ),
            },
        },
    )
