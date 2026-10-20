import logging
from collections.abc import Iterable
from difflib import SequenceMatcher

from src.domains.green_claim.entities.green_claim_models import (
    GreenClaimLLMResponse,
    GreenClaimReportItem,
    PrincipleViolation,
)

logger = logging.getLogger(__name__)


class ResponseComparator:
    """
    Compares and merges two GreenClaimLLMResponse objects.

    - Principle violations found in both responses → confidence="strong"
    - Principle violations found in only one response → confidence="weak"

    Items are matched based on statement similarity.
    Individual principle violations within matched statements are merged with confidence levels.
    """

    def __init__(self, statement_similarity_threshold: float = 0.85):
        self._similarity_threshold = statement_similarity_threshold
        logger.debug(
            "ResponseComparator initialized "
            f"(similarity_threshold={statement_similarity_threshold})"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def merge_responses(
        self,
        response1: GreenClaimLLMResponse,
        response2: GreenClaimLLMResponse,
    ) -> GreenClaimLLMResponse:
        """
        Merge two LLM responses, assigning confidence levels.
        """
        logger.debug(
            "Merging responses: "
            f"response1={len(response1.green_claim_report)} items, "
            f"response2={len(response2.green_claim_report)} items"
        )

        merged_items: list[GreenClaimReportItem] = []
        matched_items_from_response2: set[int] = set()

        for item1 in response1.green_claim_report:
            match = self._find_matching_item(item1, response2.green_claim_report)

            if match:
                matched_items_from_response2.add(match["index"])
                # Merge principle violations with confidence levels
                merged_item = self._merge_principle_violations(item1, match["item"])
                merged_items.append(merged_item)
            else:
                # No match found - all violations are weak
                merged_items.append(self._build_weak_item(item1))

        # Add remaining unmatched items from response2
        merged_items.extend(
            self._build_unmatched_items(
                response2.green_claim_report,
                matched_items_from_response2,
            )
        )

        self._log_merge_summary(merged_items)

        return GreenClaimLLMResponse(
            green_claim_report=merged_items,
            legal_statement=response1.legal_statement,
            guidelines=response1.guidelines,
        )

    # ------------------------------------------------------------------
    # Matching logic
    # ------------------------------------------------------------------

    def _find_matching_item(
        self,
        item: GreenClaimReportItem,
        candidates: list[GreenClaimReportItem],
    ) -> dict | None:
        """
        Find a matching item by fuzzy statement similarity.
        """
        for index, candidate in enumerate(candidates):
            if self._statements_are_similar(item.statement, candidate.statement):
                return {"item": candidate, "index": index}

        return None

    def _statements_are_similar(self, stmt1: str, stmt2: str) -> bool:
        return self._similarity(stmt1, stmt2) >= self._similarity_threshold

    # ------------------------------------------------------------------
    # Item builders
    # ------------------------------------------------------------------

    def _merge_principle_violations(
        self,
        item1: GreenClaimReportItem,
        item2: GreenClaimReportItem,
    ) -> GreenClaimReportItem:
        """
        Merge principle violations from two matched items.
        Violations found in both get confidence='strong'.
        Violations found in only one get confidence='weak'.
        """
        # Build a map of principle name -> violation for item2
        item2_violations = {v.principle: v for v in item2.principles_violated}

        merged_violations: list[PrincipleViolation] = []
        matched_principles: set[str] = set()

        # Process violations from item1
        for v1 in item1.principles_violated:
            if v1.principle in item2_violations:
                # Found in both - mark as strong
                matched_principles.add(v1.principle)
                merged_violations.append(
                    PrincipleViolation(
                        principle=v1.principle,
                        explanation=v1.explanation,
                        guidelines_reference=v1.guidelines_reference,
                        improvement_recommendation=v1.improvement_recommendation,
                        confidence="strong",
                    )
                )
                logger.debug(f"Strong match for principle: {v1.principle}")
            else:
                # Only in item1 - mark as weak
                merged_violations.append(
                    PrincipleViolation(
                        principle=v1.principle,
                        explanation=v1.explanation,
                        guidelines_reference=v1.guidelines_reference,
                        improvement_recommendation=v1.improvement_recommendation,
                        confidence="weak",
                    )
                )
                logger.debug(f"Weak match for principle (only in response1): {v1.principle}")

        # Add violations from item2 that weren't in item1
        for v2 in item2.principles_violated:
            if v2.principle not in matched_principles:
                merged_violations.append(
                    PrincipleViolation(
                        principle=v2.principle,
                        explanation=v2.explanation,
                        guidelines_reference=v2.guidelines_reference,
                        improvement_recommendation=v2.improvement_recommendation,
                        confidence="weak",
                    )
                )
                logger.debug(f"Weak match for principle (only in response2): {v2.principle}")

        # Sort by principle name to maintain consistent ordering
        merged_violations.sort(key=lambda v: v.principle)

        return GreenClaimReportItem(
            statement=item1.statement,
            principles_violated=merged_violations,
        )

    def _build_weak_item(self, item: GreenClaimReportItem) -> GreenClaimReportItem:
        """Mark all principle violations as weak (found in only one response)."""
        weak_violations = [
            PrincipleViolation(
                principle=v.principle,
                explanation=v.explanation,
                guidelines_reference=v.guidelines_reference,
                improvement_recommendation=v.improvement_recommendation,
                confidence="weak",
            )
            for v in item.principles_violated
        ]
        return GreenClaimReportItem(
            statement=item.statement,
            principles_violated=weak_violations,
        )

    def _build_unmatched_items(
        self,
        items: Iterable[GreenClaimReportItem],
        used_indices: set[int],
    ) -> list[GreenClaimReportItem]:
        unmatched: list[GreenClaimReportItem] = []

        for index, item in enumerate(items):
            if index in used_indices:
                continue

            unmatched.append(self._build_weak_item(item))

        return unmatched

    # ------------------------------------------------------------------
    # Similarity helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize(statement: str) -> str:
        return " ".join(statement.lower().strip().split())

    def _similarity(self, a: str, b: str) -> float:
        norm_a = self._normalize(a)
        norm_b = self._normalize(b)
        return SequenceMatcher(None, norm_a, norm_b).ratio()

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    @staticmethod
    def _log_merge_summary(items: list[GreenClaimReportItem]) -> None:
        total_violations = sum(len(item.principles_violated) for item in items)
        strong = sum(
            1 for item in items for v in item.principles_violated if v.confidence == "strong"
        )
        weak = sum(1 for item in items for v in item.principles_violated if v.confidence == "weak")

        logger.info(
            f"Merge complete: {len(items)} items, "
            f"{total_violations} principle violations ({strong} strong, {weak} weak)"
        )
