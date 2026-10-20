from difflib import SequenceMatcher

import pytest

from src.domains.green_claim.services.problematic_word_matcher import (
    ProblematicWordMatcher,
)


@pytest.fixture
def matcher():
    return ProblematicWordMatcher(
        problematic_words=[
            "carbon neutral",
            "eco friendly",
            "sustainable",
        ],
        similarity_threshold=0.85,
    )


def test_exact_single_word_match(matcher):
    text = "This product is sustainable."

    result = matcher.find(text)

    assert len(result.exact_matches) == 1
    match = result.exact_matches[0]

    assert match.text == "sustainable"
    assert match.type == "exact"
    assert match.confidence == pytest.approx(1.0)


def test_exact_multi_word_match(matcher):
    text = "We claim to be carbon neutral by 2030."

    result = matcher.find(text)

    assert any(m.text == "carbon neutral" and m.type == "exact" for m in result.exact_matches)


def test_case_and_whitespace_normalization(matcher):
    text = "Our process is   ECO    FRIENDLY."

    result = matcher.find(text)

    assert any(m.text == "eco friendly" and m.type == "exact" for m in result.exact_matches)


def test_fuzzy_match_above_threshold(matcher):
    text = "This product is sustainble."

    result = matcher.find(text)

    assert len(result.close_matches) == 1
    match = result.close_matches[0]

    assert match.matched_against == "sustainable"
    assert match.type == "close"
    assert match.confidence >= 0.85


def test_no_match_below_threshold(matcher):
    text = "This product is recyclable."

    result = matcher.find(text)

    assert result.exact_matches == []
    assert result.close_matches == []


def test_does_not_cross_match_phrase_lengths(matcher):
    text = "This product is carbon."

    result = matcher.find(text)

    # "carbon" must not match "carbon neutral"
    assert result.exact_matches == []
    assert result.close_matches == []


def test_multiple_matches_in_same_text(matcher):
    text = "Our eco friendly and sustainable solution is carbon neutral."

    result = matcher.find(text)

    exact_texts = {m.text for m in result.exact_matches}

    assert exact_texts == {
        "eco friendly",
        "sustainable",
        "carbon neutral",
    }


def test_start_and_end_indices_single_word(matcher):
    text = "This product is sustainable."

    result = matcher.find(text)

    assert len(result.exact_matches) == 1
    match = result.exact_matches[0]

    assert match.start == 16
    assert match.end == 27
    assert text[match.start : match.end] == "sustainable"


def test_start_and_end_indices_multi_word(matcher):
    text = "We claim to be carbon neutral by 2030."

    result = matcher.find(text)

    carbon_match = next(m for m in result.exact_matches if m.text == "carbon neutral")

    assert carbon_match.start == 15
    assert carbon_match.end == 29
    assert text[carbon_match.start : carbon_match.end] == "carbon neutral"


def test_indices_with_punctuation_and_whitespace(matcher):
    text = "Our process is   ECO    FRIENDLY!"

    result = matcher.find(text)

    eco_match = next(m for m in result.exact_matches if m.text == "eco friendly")

    # Should capture from start of "ECO" to end of "FRIENDLY" (not including punctuation)
    assert eco_match.start == 17
    assert eco_match.end == 32
    # Verify we can extract the original text
    extracted = text[eco_match.start : eco_match.end]
    assert extracted == "ECO    FRIENDLY"


def test_indices_for_fuzzy_matches(matcher):
    text = "This product is sustainble."

    result = matcher.find(text)

    assert len(result.close_matches) == 1
    match = result.close_matches[0]

    assert match.start == 16
    assert match.end == 26
    assert text[match.start : match.end] == "sustainble"


def test_repeated_ngram_yields_one_match_per_occurrence(matcher):
    text = "sustainble here and sustainble there."

    result = matcher.find(text)

    assert len(result.close_matches) == 2

    spans = sorted((m.start, m.end) for m in result.close_matches)
    assert spans == [(0, 10), (20, 30)]

    for match in result.close_matches:
        assert match.matched_against == "sustainable"
        assert text[match.start : match.end] == "sustainble"


def test_repeated_multi_word_ngram_keeps_distinct_spans(matcher):
    text = "carbon nutral and carbon nutral again."

    result = matcher.find(text)

    assert len(result.close_matches) == 2

    spans = sorted((m.start, m.end) for m in result.close_matches)
    assert spans == [(0, 13), (18, 31)]

    for match in result.close_matches:
        assert match.matched_against == "carbon neutral"
        assert text[match.start : match.end] == "carbon nutral"


def test_close_match_confidence_is_exact_sequencematcher_ratio(matcher):
    text = "This product is sustainble."

    result = matcher.find(text)

    expected = round(SequenceMatcher(None, "sustainble", "sustainable").ratio(), 3)
    assert result.close_matches[0].confidence == pytest.approx(expected)


def test_exact_and_close_matches_in_one_pass(matcher):
    text = "Our eco friendly and sustainble packaging is carbon neutral."

    result = matcher.find(text)

    assert {m.text for m in result.exact_matches} == {"eco friendly", "carbon neutral"}
    assert [m.matched_against for m in result.close_matches] == ["sustainable"]


def test_indices_for_multiple_matches(matcher):
    text = "Our eco friendly and sustainable solution is carbon neutral."

    result = matcher.find(text)

    matches_by_text = {m.text: m for m in result.exact_matches}

    # Verify eco friendly
    eco_match = matches_by_text["eco friendly"]
    assert eco_match.start == 4
    assert eco_match.end == 16
    assert text[eco_match.start : eco_match.end] == "eco friendly"

    # Verify sustainable
    sustainable_match = matches_by_text["sustainable"]
    assert sustainable_match.start == 21
    assert sustainable_match.end == 32
    assert text[sustainable_match.start : sustainable_match.end] == "sustainable"

    # Verify carbon neutral
    carbon_match = matches_by_text["carbon neutral"]
    assert carbon_match.start == 45
    assert carbon_match.end == 59
    assert text[carbon_match.start : carbon_match.end] == "carbon neutral"
