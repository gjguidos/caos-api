import pytest

from src.domains.green_claim.entities.green_claim_models import (
    GreenClaimLLMResponse,
    GreenClaimReportItem,
    PrincipleViolation,
)
from src.domains.green_claim.services.response_comparator import ResponseComparator


@pytest.fixture
def comparator():
    return ResponseComparator(statement_similarity_threshold=0.85)


@pytest.fixture
def sample_item1():
    return GreenClaimReportItem(
        statement="Made from 100% recycled materials",
        principles_violated=[
            PrincipleViolation(
                principle="Truthfulness",
                explanation="This is a clear environmental claim",
                guidelines_reference=["1. Truthfulness"],
                improvement_recommendation="Provide certification details",
            )
        ],
    )


@pytest.fixture
def sample_item2():
    return GreenClaimReportItem(
        statement="Reduces carbon emissions by 50%",
        principles_violated=[
            PrincipleViolation(
                principle="Substantiation",
                explanation="Quantifiable environmental claim",
                guidelines_reference=["2. Substantiation"],
                improvement_recommendation="Include methodology and verification",
            )
        ],
    )


@pytest.fixture
def sample_response1(sample_item1, sample_item2):
    return GreenClaimLLMResponse(
        green_claim_report=[sample_item1, sample_item2],
        legal_statement="Legal disclaimer",
        guidelines="https://example.com/guidelines",
    )


def test_merge_both_items_match_creates_strong_confidence(comparator):
    """Principle violations found in both responses should have strong confidence."""
    item1 = GreenClaimReportItem(
        statement="Made from 100% recycled materials",
        principles_violated=[
            PrincipleViolation(
                principle="Truthfulness",
                explanation="Test explanation",
                guidelines_reference=["1. Truthfulness"],
                improvement_recommendation="Test recommendation",
            )
        ],
    )

    response1 = GreenClaimLLMResponse(
        green_claim_report=[item1],
        legal_statement="Legal",
        guidelines="https://example.com",
    )
    response2 = GreenClaimLLMResponse(
        green_claim_report=[item1],
        legal_statement="Legal",
        guidelines="https://example.com",
    )

    result = comparator.merge_responses(response1, response2)

    assert len(result.green_claim_report) == 1
    assert len(result.green_claim_report[0].principles_violated) == 1
    assert result.green_claim_report[0].principles_violated[0].confidence == "strong"


def test_merge_unmatched_items_have_weak_confidence(comparator):
    """Items found in only one response should have weak confidence."""
    item1 = GreenClaimReportItem(
        statement="Made from recycled materials",
        principles_violated=[
            PrincipleViolation(
                principle="Truthfulness",
                explanation="Test",
                guidelines_reference=["1. Truthfulness"],
                improvement_recommendation="Test",
            )
        ],
    )
    item2 = GreenClaimReportItem(
        statement="Reduces emissions",
        principles_violated=[
            PrincipleViolation(
                principle="Substantiation",
                explanation="Test",
                guidelines_reference=["2. Substantiation"],
                improvement_recommendation="Test",
            )
        ],
    )

    response1 = GreenClaimLLMResponse(
        green_claim_report=[item1],
        legal_statement="Legal",
        guidelines="https://example.com",
    )
    response2 = GreenClaimLLMResponse(
        green_claim_report=[item2],
        legal_statement="Legal",
        guidelines="https://example.com",
    )

    result = comparator.merge_responses(response1, response2)

    assert len(result.green_claim_report) == 2
    # Check that all principle violations have weak confidence
    for item in result.green_claim_report:
        for violation in item.principles_violated:
            assert violation.confidence == "weak"


def test_merge_similar_statements_match(comparator):
    """Statements with high similarity should match even if not identical."""
    item1 = GreenClaimReportItem(
        statement="Made from   100%   recycled materials",
        principles_violated=[
            PrincipleViolation(
                principle="Truthfulness",
                explanation="Test",
                guidelines_reference=["1. Truthfulness"],
                improvement_recommendation="Test",
            )
        ],
    )
    item2 = GreenClaimReportItem(
        statement="made from 100% recycled materials",
        principles_violated=[
            PrincipleViolation(
                principle="Truthfulness",
                explanation="Test",
                guidelines_reference=["1. Truthfulness"],
                improvement_recommendation="Test",
            )
        ],
    )

    response1 = GreenClaimLLMResponse(
        green_claim_report=[item1],
        legal_statement="Legal",
        guidelines="https://example.com",
    )
    response2 = GreenClaimLLMResponse(
        green_claim_report=[item2],
        legal_statement="Legal",
        guidelines="https://example.com",
    )

    result = comparator.merge_responses(response1, response2)

    assert len(result.green_claim_report) == 1
    assert len(result.green_claim_report[0].principles_violated) == 1
    assert result.green_claim_report[0].principles_violated[0].confidence == "strong"


def test_merge_different_statements_do_not_match(comparator):
    """Items with different statements should not match."""
    item1 = GreenClaimReportItem(
        statement="Made from recycled materials",
        principles_violated=[
            PrincipleViolation(
                principle="Truthfulness",
                explanation="Test",
                guidelines_reference=["1. Truthfulness"],
                improvement_recommendation="Test",
            )
        ],
    )
    item2 = GreenClaimReportItem(
        statement="Carbon neutral product",
        principles_violated=[
            PrincipleViolation(
                principle="Substantiation",
                explanation="Test",
                guidelines_reference=["2. Substantiation"],
                improvement_recommendation="Test",
            )
        ],
    )

    response1 = GreenClaimLLMResponse(
        green_claim_report=[item1],
        legal_statement="Legal",
        guidelines="https://example.com",
    )
    response2 = GreenClaimLLMResponse(
        green_claim_report=[item2],
        legal_statement="Legal",
        guidelines="https://example.com",
    )

    result = comparator.merge_responses(response1, response2)

    assert len(result.green_claim_report) == 2
    # Check that all principle violations have weak confidence
    for item in result.green_claim_report:
        for violation in item.principles_violated:
            assert violation.confidence == "weak"


def test_merge_empty_responses(comparator):
    """Merging empty responses should return empty report."""
    response1 = GreenClaimLLMResponse(
        green_claim_report=[],
        legal_statement="Legal",
        guidelines="https://example.com",
    )
    response2 = GreenClaimLLMResponse(
        green_claim_report=[],
        legal_statement="Legal",
        guidelines="https://example.com",
    )

    result = comparator.merge_responses(response1, response2)

    assert len(result.green_claim_report) == 0


def test_merge_preserves_response1_metadata(sample_response1):
    """Merged response should preserve legal_statement and guidelines from response1."""
    response2 = GreenClaimLLMResponse(
        green_claim_report=[],
        legal_statement="Different legal",
        guidelines="https://different.com",
    )

    result = ResponseComparator().merge_responses(sample_response1, response2)

    assert result.legal_statement == sample_response1.legal_statement
    assert result.guidelines == sample_response1.guidelines


def test_similarity_threshold_affects_matching():
    """Lower similarity threshold should allow more matches."""
    strict_comparator = ResponseComparator(statement_similarity_threshold=0.95)
    lenient_comparator = ResponseComparator(statement_similarity_threshold=0.70)

    item1 = GreenClaimReportItem(
        statement="Made from recycled materials",
        principles_violated=[
            PrincipleViolation(
                principle="Truthfulness",
                explanation="Test",
                guidelines_reference=["1. Truthfulness"],
                improvement_recommendation="Test",
            )
        ],
    )
    item2 = GreenClaimReportItem(
        statement="Made from mostly recycled materials",
        principles_violated=[
            PrincipleViolation(
                principle="Truthfulness",
                explanation="Test",
                guidelines_reference=["1. Truthfulness"],
                improvement_recommendation="Test",
            )
        ],
    )

    response1 = GreenClaimLLMResponse(
        green_claim_report=[item1],
        legal_statement="Legal",
        guidelines="https://example.com",
    )
    response2 = GreenClaimLLMResponse(
        green_claim_report=[item2],
        legal_statement="Legal",
        guidelines="https://example.com",
    )

    strict_result = strict_comparator.merge_responses(response1, response2)
    lenient_result = lenient_comparator.merge_responses(response1, response2)

    # Strict should treat them as different (2 weak items with weak violations)
    assert len(strict_result.green_claim_report) == 2
    for item in strict_result.green_claim_report:
        for violation in item.principles_violated:
            assert violation.confidence == "weak"

    # Lenient should match them (1 item with strong violation)
    assert len(lenient_result.green_claim_report) == 1
    assert lenient_result.green_claim_report[0].principles_violated[0].confidence == "strong"


def test_merge_complex_scenario():
    """Test a complex scenario with multiple matches and non-matches."""
    items_r1 = [
        GreenClaimReportItem(
            statement="100% recycled",
            principles_violated=[
                PrincipleViolation(
                    principle="Truthfulness",
                    explanation="Test",
                    guidelines_reference=["1. Truthfulness"],
                    improvement_recommendation="Test",
                )
            ],
        ),
        GreenClaimReportItem(
            statement="Carbon neutral",
            principles_violated=[
                PrincipleViolation(
                    principle="Substantiation",
                    explanation="Test",
                    guidelines_reference=["2. Substantiation"],
                    improvement_recommendation="Test",
                )
            ],
        ),
    ]
    items_r2 = [
        GreenClaimReportItem(
            statement="100% recycled",
            principles_violated=[
                PrincipleViolation(
                    principle="Truthfulness",
                    explanation="Test",
                    guidelines_reference=["1. Truthfulness"],
                    improvement_recommendation="Test",
                )
            ],
        ),
        GreenClaimReportItem(
            statement="Eco-friendly",
            principles_violated=[
                PrincipleViolation(
                    principle="Clarity",
                    explanation="Test",
                    guidelines_reference=["3. Clarity"],
                    improvement_recommendation="Test",
                )
            ],
        ),
    ]

    response1 = GreenClaimLLMResponse(
        green_claim_report=items_r1,
        legal_statement="Legal",
        guidelines="https://example.com",
    )
    response2 = GreenClaimLLMResponse(
        green_claim_report=items_r2,
        legal_statement="Legal",
        guidelines="https://example.com",
    )

    result = ResponseComparator().merge_responses(response1, response2)

    assert len(result.green_claim_report) == 3

    # Count items with strong vs weak principle violations
    items_with_strong = [
        item
        for item in result.green_claim_report
        if any(v.confidence == "strong" for v in item.principles_violated)
    ]
    items_with_only_weak = [
        item
        for item in result.green_claim_report
        if all(v.confidence == "weak" for v in item.principles_violated)
    ]

    assert len(items_with_strong) == 1  # "100% recycled" matched
    assert len(items_with_only_weak) == 2  # "Carbon neutral" and "Eco-friendly" unmatched


def test_normalization_handles_case_and_whitespace():
    """Test that normalization properly handles case and whitespace."""
    comparator = ResponseComparator()

    normalized = comparator._normalize("  THIS   is   A   TEST  ")

    assert normalized == "this is a test"


def test_merge_same_statement_different_principles():
    """Same statement can have different principles with mixed confidence levels."""
    comparator = ResponseComparator()

    # Response 1: statement violates principles A and B
    item1 = GreenClaimReportItem(
        statement="100% eco-friendly product",
        principles_violated=[
            PrincipleViolation(
                principle="Clarity and Specificity",
                explanation="Vague claim",
                guidelines_reference=["4. Scope"],
                improvement_recommendation="Be specific",
            ),
            PrincipleViolation(
                principle="Scientific Evidence and Substantiation",
                explanation="No evidence",
                guidelines_reference=["5. Reasonable basis"],
                improvement_recommendation="Provide evidence",
            ),
        ],
    )

    # Response 2: same statement violates principle A and C
    item2 = GreenClaimReportItem(
        statement="100% eco-friendly product",
        principles_violated=[
            PrincipleViolation(
                principle="Clarity and Specificity",
                explanation="Vague claim",
                guidelines_reference=["4. Scope"],
                improvement_recommendation="Be specific",
            ),
            PrincipleViolation(
                principle="Truthfulness and Accuracy",
                explanation="Absolute claim",
                guidelines_reference=["1. Truthfulness"],
                improvement_recommendation="Qualify claim",
            ),
        ],
    )

    response1 = GreenClaimLLMResponse(
        green_claim_report=[item1],
        legal_statement="Legal",
        guidelines="https://example.com",
    )
    response2 = GreenClaimLLMResponse(
        green_claim_report=[item2],
        legal_statement="Legal",
        guidelines="https://example.com",
    )

    result = comparator.merge_responses(response1, response2)

    # Should have 1 merged item
    assert len(result.green_claim_report) == 1
    merged_item = result.green_claim_report[0]

    # Should have 3 principle violations total (A, B, C)
    assert len(merged_item.principles_violated) == 3

    # Find each principle
    violations_by_principle = {v.principle: v for v in merged_item.principles_violated}

    # Principle A (Clarity) should be strong (found in both)
    assert "Clarity and Specificity" in violations_by_principle
    assert violations_by_principle["Clarity and Specificity"].confidence == "strong"

    # Principle B (Substantiation) should be weak (only in response 1)
    assert "Scientific Evidence and Substantiation" in violations_by_principle
    assert violations_by_principle["Scientific Evidence and Substantiation"].confidence == "weak"

    # Principle C (Truthfulness) should be weak (only in response 2)
    assert "Truthfulness and Accuracy" in violations_by_principle
    assert violations_by_principle["Truthfulness and Accuracy"].confidence == "weak"


def test_similarity_calculation():
    """Test similarity calculation between statements."""
    comparator = ResponseComparator()

    # Identical strings
    assert abs(comparator._similarity("test", "test") - 1.0) < 0.001

    # Very similar strings
    similarity = comparator._similarity(
        "Made from recycled materials",
        "made from recycled materials",
    )
    assert similarity >= 0.95

    # Different strings
    similarity = comparator._similarity("completely different", "totally unique")
    assert similarity < 0.5
