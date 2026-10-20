import re
from collections.abc import Iterator
from difflib import SequenceMatcher

from src.domains.green_claim.entities.green_claim_models import (
    ProblematicWordMatch,
    ProblematicWordResult,
)


class ProblematicWordMatcher:
    """
    Matches text against problematic words or phrases using:
    - Exact n-gram matching
    - Fuzzy similarity matching (SequenceMatcher)
    """

    def __init__(
        self,
        problematic_words: list[str],
        similarity_threshold: float,
    ):
        self._threshold = similarity_threshold

        # Normalize and group problematic phrases by token length
        self._problematic_by_len = self._group_problematic_words(problematic_words)

        # Precompute exact lookup sets for O(1) matching
        self._exact_sets = {
            length: set(phrases) for length, phrases in self._problematic_by_len.items()
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def find(self, text: str) -> ProblematicWordResult:
        """
        Find exact and close matches of problematic words in the given text.
        """
        tokens = self._tokenize_with_positions(text)

        exact_matches: list[ProblematicWordMatch] = []
        close_matches: list[ProblematicWordMatch] = []

        for length, problematic_phrases in self._problematic_by_len.items():
            if len(tokens) < length:
                continue

            # One SequenceMatcher per phrase, built per call so that concurrent
            # find() calls never share these mutable objects. Holding the phrase
            # as seq2 means difflib's b2j index is built once per phrase instead
            # of once per (n-gram, phrase) pair.
            matchers = [
                (phrase, SequenceMatcher(None, "", phrase)) for phrase in problematic_phrases
            ]
            scored_candidates: dict[str, list[tuple[str, float]]] = {}

            for ngram in self._generate_ngrams(tokens, length):
                candidate = ngram["text"]

                if self._is_exact_match(candidate, length):
                    exact_matches.append(
                        self._build_match(
                            candidate=candidate,
                            matched_against=candidate,
                            confidence=1.0,
                            match_type="exact",
                            ngram=ngram,
                        )
                    )
                    continue

                scored = scored_candidates.get(candidate)
                if scored is None:
                    scored = self._score_candidate(candidate, matchers)
                    scored_candidates[candidate] = scored

                close_matches.extend(
                    self._build_match(
                        candidate=candidate,
                        matched_against=phrase,
                        confidence=round(score, 3),
                        match_type="close",
                        ngram=ngram,
                    )
                    for phrase, score in scored
                )

        return ProblematicWordResult(
            exact_matches=exact_matches,
            close_matches=close_matches,
        )

    # ------------------------------------------------------------------
    # Matching logic
    # ------------------------------------------------------------------

    def _is_exact_match(self, candidate: str, length: int) -> bool:
        return candidate in self._exact_sets.get(length, set())

    def _score_candidate(
        self,
        candidate: str,
        matchers: list[tuple[str, SequenceMatcher]],
    ) -> list[tuple[str, float]]:
        """
        Score one n-gram against every problematic phrase of the same length.

        real_quick_ratio() and quick_ratio() are upper bounds on ratio(), so
        rejecting on them cannot discard a phrase that would have passed.
        """
        scored: list[tuple[str, float]] = []

        for phrase, matcher in matchers:
            matcher.set_seq1(candidate)

            if matcher.real_quick_ratio() < self._threshold:
                continue
            if matcher.quick_ratio() < self._threshold:
                continue

            score = matcher.ratio()
            if score >= self._threshold:
                scored.append((phrase, score))

        return scored

    def _build_match(
        self,
        candidate: str,
        matched_against: str,
        confidence: float,
        match_type: str,
        ngram: dict,
    ) -> ProblematicWordMatch:
        return ProblematicWordMatch(
            text=candidate,
            matched_against=matched_against,
            confidence=confidence,
            type=match_type,
            start=ngram["start"],
            end=ngram["end"],
        )

    # ------------------------------------------------------------------
    # Preprocessing helpers
    # ------------------------------------------------------------------

    def _group_problematic_words(self, problematic_words: list[str]) -> dict[int, list[str]]:
        grouped: dict[int, list[str]] = {}

        for word in problematic_words:
            normalized = self._normalize(word)
            length = len(normalized.split())
            grouped.setdefault(length, []).append(normalized)

        return grouped

    @staticmethod
    def _normalize(text: str) -> str:
        """
        Lowercase, remove punctuation, and normalize whitespace.
        """
        text = re.sub(r"[^\w\s]", " ", text.lower())
        return re.sub(r"\s+", " ", text).strip()

    # ------------------------------------------------------------------
    # Tokenization & n-grams
    # ------------------------------------------------------------------

    @staticmethod
    def _tokenize_with_positions(text: str) -> list[dict]:
        """
        Tokenize text while preserving original character positions.
        """
        tokens: list[dict] = []

        for match in re.finditer(r"\w+", text):
            tokens.append(
                {
                    "text": match.group().lower(),
                    "start": match.start(),
                    "end": match.end(),
                }
            )

        return tokens

    @staticmethod
    def _generate_ngrams(tokens: list[dict], n: int) -> Iterator[dict]:
        """
        Generate n-grams with normalized text and original span.
        """
        for i in range(len(tokens) - n + 1):
            slice_ = tokens[i : i + n]
            yield {
                "text": " ".join(token["text"] for token in slice_),
                "start": slice_[0]["start"],
                "end": slice_[-1]["end"],
            }
