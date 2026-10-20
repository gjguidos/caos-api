"""
Audit Utilities

Helper functions for green claim audit logging.
"""

import hashlib
from typing import Any

from src.domains.green_claim.entities.green_claim_models import GreenClaimResponse


def compute_text_hash(text: str) -> str:
    """
    Compute SHA-256 hash of text content.

    Args:
        text: Text to hash

    Returns:
        Hex string of SHA-256 hash
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_file_hash(file_data: bytes) -> str:
    """
    Compute SHA-256 hash of file content.

    Args:
        file_data: Binary file data

    Returns:
        Hex string of SHA-256 hash
    """
    return hashlib.sha256(file_data).hexdigest()


def summarize_output(response: GreenClaimResponse) -> dict[str, Any]:
    """
    Create a compact summary of analysis output for audit logging.

    Args:
        response: The full green claim analysis response

    Returns:
        Dictionary with key metrics
    """
    total_violations = sum(len(item.principles_violated) for item in response.green_claim_report)

    strong_violations = sum(
        1
        for item in response.green_claim_report
        for violation in item.principles_violated
        if violation.confidence == "strong"
    )

    weak_violations = sum(
        1
        for item in response.green_claim_report
        for violation in item.principles_violated
        if violation.confidence == "weak"
    )

    return {
        "green_claims_found": len(response.green_claim_report),
        "total_violations": total_violations,
        "strong_violations": strong_violations,
        "weak_violations": weak_violations,
        "problematic_words_exact": len(response.problematic_words.exact_matches),
        "problematic_words_close": len(response.problematic_words.close_matches),
        "problematic_words_total": response.problematic_words.total_count,
        "has_violations": total_violations > 0,
        "has_strong_confidence_violations": strong_violations > 0,
    }
