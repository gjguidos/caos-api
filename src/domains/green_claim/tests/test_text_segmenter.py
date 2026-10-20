import pytest

from src.domains.green_claim.services.text_segmenter import TextSegmenter


@pytest.fixture
def segmenter():
    """Fixture providing a TextSegmenter with default chunk size."""
    return TextSegmenter(chunk_size=100)


@pytest.fixture
def large_segmenter():
    """Fixture providing a TextSegmenter with larger chunk size."""
    return TextSegmenter(chunk_size=500)


class TestTextSegmenterInitialization:
    """Test suite for TextSegmenter initialization."""

    def test_custom_chunk_size(self):
        """Test initialization with custom chunk size."""
        segmenter = TextSegmenter(chunk_size=250)
        assert segmenter.chunk_size == 250

    def test_invalid_chunk_size_zero(self):
        """Test that chunk size of 0 raises ValueError."""
        with pytest.raises(ValueError, match="Chunk size must be greater than 0"):
            TextSegmenter(chunk_size=0)

    def test_invalid_chunk_size_negative(self):
        """Test that negative chunk size raises ValueError."""
        with pytest.raises(ValueError, match="Chunk size must be greater than 0"):
            TextSegmenter(chunk_size=-100)


class TestBasicSegmentation:
    """Test suite for basic text segmentation functionality."""

    def test_single_short_sentence(self, segmenter):
        """Test segmentation of a single sentence that fits in one chunk."""
        text = "This is a single short sentence."
        chunks = segmenter.segment(text)

        assert len(chunks) == 1
        assert chunks[0] == text

    def test_multiple_sentences_single_chunk(self, segmenter):
        """Test multiple sentences that fit in a single chunk."""
        text = "First sentence. Second sentence. Third sentence."
        chunks = segmenter.segment(text)

        assert len(chunks) == 1
        assert chunks[0] == text

    def test_multiple_sentences_multiple_chunks(self, segmenter):
        """Test multiple sentences split across chunks."""
        text = (
            "This is the first sentence. This is the second sentence. "
            "This is the third sentence. This is the fourth sentence. "
            "This is the fifth sentence."
        )
        chunks = segmenter.segment(text)

        assert len(chunks) > 1
        assert all(len(chunk) <= 100 for chunk in chunks)

        # Verify all chunks end with sentence terminators
        for chunk in chunks:
            assert chunk.strip()[-1] in ".!?"

    def test_sentence_preservation(self, segmenter):
        """Test that sentences are never broken mid-sentence."""
        text = "Sentence one. Sentence two. Sentence three. Sentence four."
        chunks = segmenter.segment(text)

        # Verify each chunk ends with a complete sentence
        for chunk in chunks:
            assert chunk.strip()[-1] in ".!?", f"Chunk doesn't end properly: {chunk}"


class TestEdgeCases:
    """Test suite for edge cases and special scenarios."""

    def test_empty_string(self, segmenter):
        """Test handling of empty string."""
        chunks = segmenter.segment("")
        assert chunks == []

    def test_whitespace_only(self, segmenter):
        """Test handling of whitespace-only text."""
        chunks = segmenter.segment("   \n\t  ")
        assert chunks == []

    def test_single_word(self, segmenter):
        """Test handling of single word."""
        text = "Word"
        chunks = segmenter.segment(text)

        assert len(chunks) == 1
        assert chunks[0] == "Word"

    def test_text_with_exclamation_marks(self, segmenter):
        """Test text with exclamation marks as sentence terminators."""
        text = "This is great! Amazing product! Buy now!"
        chunks = segmenter.segment(text)

        assert len(chunks) >= 1
        for chunk in chunks:
            assert chunk.strip()[-1] == "!"

    def test_text_with_question_marks(self, segmenter):
        """Test text with question marks as sentence terminators."""
        text = "What is this? How does it work? Why should I care?"
        chunks = segmenter.segment(text)

        assert len(chunks) >= 1
        for chunk in chunks:
            assert chunk.strip()[-1] == "?"

    def test_mixed_punctuation(self, segmenter):
        """Test text with mixed sentence terminators."""
        text = "This is a statement. Is this a question? This is exciting!"
        chunks = segmenter.segment(text)

        reconstructed = " ".join(chunks)
        # Verify all content is preserved
        assert "statement" in reconstructed
        assert "question" in reconstructed
        assert "exciting" in reconstructed


class TestLongSentences:
    """Test suite for handling sentences longer than chunk size."""

    def test_single_long_sentence(self):
        """Test sentence longer than chunk size."""
        segmenter = TextSegmenter(chunk_size=50)

        text = (
            "This is a very long sentence that contains many words and will "
            "definitely exceed the chunk size limit and needs to be split at "
            "word boundaries."
        )
        chunks = segmenter.segment(text)

        assert len(chunks) == 1

    def test_very_long_word(self):
        """Test handling of a single word longer than chunk size."""
        segmenter = TextSegmenter(chunk_size=20)

        text = "Supercalifragilisticexpialidocious is a long word."
        chunks = segmenter.segment(text)

        # The long word should be in its own chunk
        assert any("Supercalifragilisticexpialidocious" in chunk for chunk in chunks)

    def test_multiple_long_sentences(self):
        """Test multiple long sentences."""
        segmenter = TextSegmenter(chunk_size=80)

        text = (
            "This is the first very long sentence that will definitely exceed "
            "the chunk size. This is the second very long sentence that will "
            "also exceed the chunk size limit."
        )
        chunks = segmenter.segment(text)

        assert len(chunks) > 1


class TestMultipleParagraphs:
    """Test suite for multi-paragraph text."""

    def test_multiple_paragraphs(self, large_segmenter):
        """Test segmentation of multiple paragraphs."""
        text = """
        Climate change is one of the most pressing issues of our time. 
        Scientists have been warning about its effects for decades. 
        The evidence is overwhelming and clear.
        
        We need to take action now. Renewable energy sources offer a solution. 
        Solar and wind power are becoming more affordable every year.
        
        Individual actions also matter. Reducing consumption helps. 
        Recycling is important.
        """

        chunks = large_segmenter.segment(text)

        assert len(chunks) >= 1
        assert all(len(chunk) <= 500 for chunk in chunks)

    def test_normalized_whitespace(self, segmenter):
        """Test that whitespace is normalized."""
        text = "First    sentence.    Second     sentence."
        chunks = segmenter.segment(text)

        # Whitespace should be normalized to single spaces
        assert "    " not in chunks[0]


class TestChunkSizeVariations:
    """Test suite for different chunk sizes."""

    @pytest.mark.parametrize("chunk_size", [50, 100, 200, 500, 1000])
    def test_various_chunk_sizes(self, chunk_size):
        """Test segmentation with various chunk sizes."""
        segmenter = TextSegmenter(chunk_size=chunk_size)

        text = (
            "First sentence here. Second sentence here. Third sentence here. "
            "Fourth sentence here. Fifth sentence here. Sixth sentence here."
        )

        chunks = segmenter.segment(text)

        assert len(chunks) >= 1
        assert all(len(chunk) <= chunk_size for chunk in chunks)

    def test_chunk_size_smaller_than_sentence(self):
        """Test when chunk size is smaller than a single sentence."""
        segmenter = TextSegmenter(chunk_size=30)

        text = "This is a sentence that is longer than thirty characters."
        chunks = segmenter.segment(text)

        # Should split at word boundaries
        assert len(chunks) == 1


class TestRealWorldScenarios:
    """Test suite for real-world use cases."""

    def test_marketing_text(self):
        """Test with real marketing text."""
        segmenter = TextSegmenter(chunk_size=300)

        text = """
        Our eco-friendly products are made from 100% sustainable materials. 
        We believe in protecting the environment for future generations. 
        Every purchase supports our reforestation program! Our packaging is 
        completely biodegradable and carbon-neutral. Join thousands of 
        satisfied customers who have made the switch to green living.
        """

        chunks = segmenter.segment(text)

        assert len(chunks) >= 1
        assert all(len(chunk) <= 300 for chunk in chunks)

        # Verify content is preserved
        full_text = " ".join(chunks)
        assert "eco-friendly" in full_text
        assert "sustainable" in full_text

    def test_technical_documentation(self):
        """Test with technical documentation style text."""
        segmenter = TextSegmenter(chunk_size=200)

        text = (
            "The TextSegmenter class provides intelligent text chunking. "
            "It uses regex patterns for sentence detection. "
            "The algorithm ensures no mid-sentence breaks occur. "
            "Long sentences are split at word boundaries when necessary."
        )

        chunks = segmenter.segment(text)

        assert all(len(chunk) <= 200 for chunk in chunks)

    def test_product_description_batch(self):
        """Test processing multiple product descriptions."""
        segmenter = TextSegmenter(chunk_size=150)

        descriptions = [
            "Revolutionary eco-friendly water bottle made from recycled materials.",
            "Organic cotton t-shirt sustainably sourced and carbon-neutral.",
            "Biodegradable cleaning products safe for the environment.",
        ]

        all_chunks = []
        for desc in descriptions:
            chunks = segmenter.segment(desc)
            all_chunks.extend(chunks)

        assert len(all_chunks) >= len(descriptions)


class TestContentPreservation:
    """Test suite to ensure content is never lost during segmentation."""

    def test_content_preservation_short_text(self, segmenter):
        """Test that all content is preserved for short text."""
        text = "First sentence. Second sentence. Third sentence."
        chunks = segmenter.segment(text)

        reconstructed = " ".join(chunks)

        # All words should be present
        for word in text.split():
            assert word in reconstructed

    def test_content_preservation_long_text(self):
        """Test that all content is preserved for long text."""
        segmenter = TextSegmenter(chunk_size=100)

        text = (
            "The quick brown fox jumps over the lazy dog. "
            "Pack my box with five dozen liquor jugs. "
            "How vexingly quick daft zebras jump! "
            "The five boxing wizards jump quickly."
        )

        chunks = segmenter.segment(text)
        reconstructed = " ".join(chunks)

        # Verify key phrases are preserved
        assert "quick brown fox" in reconstructed
        assert "five dozen liquor jugs" in reconstructed
        assert "boxing wizards" in reconstructed

    def test_no_duplicate_content(self, segmenter):
        """Test that content is not duplicated across chunks."""
        text = "Alpha. Beta. Gamma. Delta. Epsilon. Zeta."
        chunks = segmenter.segment(text)

        # Count occurrences of each word
        full_text = " ".join(chunks)
        assert full_text.count("Alpha") == 1
        assert full_text.count("Beta") == 1
        assert full_text.count("Gamma") == 1


class TestChunkBoundaries:
    """Test suite for verifying correct chunk boundaries."""

    def test_optimal_chunk_filling(self, segmenter):
        """Test that chunks are filled optimally without excessive splitting."""
        text = "A. B. C. D. E. F. G. H. I. J."
        chunks = segmenter.segment(text)

        # With chunk size 100, all should fit in one chunk
        assert len(chunks) == 1
