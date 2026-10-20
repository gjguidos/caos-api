import re


class TextSegmenter:
    """
    Splits text into chunks of a maximum size without breaking sentences.

    Intended for preparing large texts for LLM analysis by chunking
    at sentence boundaries.
    """

    def __init__(self, chunk_size: int = 2500):
        if chunk_size <= 0:
            raise ValueError("Chunk size must be greater than 0")

        self._chunk_size = chunk_size
        self._sentence_pattern = self._compile_sentence_pattern()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def segment(self, text: str) -> list[str]:
        """
        Segment text into sentence-respecting chunks.
        """
        if not self._has_content(text):
            return []

        sentences = self._split_into_sentences(text)
        if not sentences:
            return []

        chunks: list[str] = []
        current_chunk: list[str] = []
        current_length = 0

        for sentence in sentences:
            if self._is_too_large(sentence):
                self._flush_current_chunk(chunks, current_chunk)
                chunks.append(sentence)
                current_length = 0
                continue

            if self._would_exceed_limit(current_length, current_chunk, sentence):
                self._flush_current_chunk(chunks, current_chunk)
                current_length = 0

            current_chunk.append(sentence)
            current_length += self._sentence_length_with_space(sentence, current_chunk)

        self._flush_current_chunk(chunks, current_chunk)
        return chunks

    @property
    def chunk_size(self) -> int:
        return self._chunk_size

    # ------------------------------------------------------------------
    # Chunking helpers
    # ------------------------------------------------------------------

    def _is_too_large(self, sentence: str) -> bool:
        return len(sentence) > self._chunk_size

    def _would_exceed_limit(
        self,
        current_length: int,
        current_chunk: list[str],
        sentence: str,
    ) -> bool:
        return (
            current_length + self._sentence_length_with_space(sentence, current_chunk)
            > self._chunk_size
        )

    @staticmethod
    def _flush_current_chunk(chunks: list[str], current_chunk: list[str]) -> None:
        if current_chunk:
            chunks.append(" ".join(current_chunk))
            current_chunk.clear()

    @staticmethod
    def _sentence_length_with_space(sentence: str, current_chunk: list[str]) -> int:
        # Account for space between sentences
        space = 1 if current_chunk else 0
        return len(sentence) + space

    # ------------------------------------------------------------------
    # Sentence splitting
    # ------------------------------------------------------------------

    def _split_into_sentences(self, text: str) -> list[str]:
        """
        Split text into sentences using a regex boundary heuristic.
        """
        normalized = self._normalize_whitespace(text)
        sentences = self._sentence_pattern.split(normalized)

        cleaned = [s.strip() for s in sentences if s.strip()]
        return cleaned or [normalized] if normalized else []

    @staticmethod
    def _normalize_whitespace(text: str) -> str:
        return " ".join(text.split())

    @staticmethod
    def _compile_sentence_pattern() -> re.Pattern:
        """
        Sentence boundary regex:

        - End punctuation (.!?)
        - Followed by whitespace and a capital letter
        - Or end of string
        """
        return re.compile(r"(?<=[.!?])\s+(?=[A-Z])|(?<=[.!?])$")

    @staticmethod
    def _has_content(text: str) -> bool:
        return bool(text and text.strip())
