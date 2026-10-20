import asyncio
import base64
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from src.base.repositories.openai_repository import OpenAIRepository
from src.domains.green_claim.entities.audit_models import (
    ANALYSIS_TYPE_CHUNKED_TEXT,
    ANALYSIS_TYPE_FILE,
    ANALYSIS_TYPE_TEXT,
)
from src.domains.green_claim.entities.green_claim_models import (
    GreenClaimLLMResponse,
    GreenClaimResponse,
    ProblematicWordResult,
)
from src.domains.green_claim.entities.problematic_words import PROBLEMATIC_WORDS
from src.domains.green_claim.services.green_claim_audit_service import GreenClaimAuditService
from src.domains.green_claim.services.problematic_word_matcher import ProblematicWordMatcher
from src.domains.green_claim.services.response_comparator import ResponseComparator
from src.domains.green_claim.services.text_segmenter import TextSegmenter
from src.domains.green_claim.utils.audit_utils import (
    compute_file_hash,
    compute_text_hash,
    summarize_output,
)
from src.domains.green_claim.utils.report_formatter import format_green_claim_as_markdown

logger = logging.getLogger(__name__)


class GreenClaimService:
    """
    Analyzes text for green claims using an LLM and problematic word matching.

    Supports:
    - Single-pass analysis
    - Chunked analysis for long texts
    - Optional second-opinion merging
    """

    def __init__(
        self,
        openai_repository: OpenAIRepository,
        audit_service: GreenClaimAuditService,
    ):
        self._openai_repository = openai_repository
        self._audit_service = audit_service

        # LLM configuration
        self._system_prompt = self._load_system_prompt()
        self._model = os.getenv("GREEN_CLAIM_MODEL", "gpt-5.2")
        self._temperature = float(os.getenv("GREEN_CLAIM_TEMPERATURE", "1"))

        # Thresholds and limits
        self._problematic_word_threshold = float(
            os.getenv("GREEN_CLAIM_PROBLEMATIC_WORD_THRESHOLD", "0.9")
        )
        self._chunk_size = int(os.getenv("GREEN_CLAIM_CHUNK_SIZE", "2500"))
        self._statement_similarity_threshold = float(
            os.getenv("GREEN_CLAIM_STATEMENT_SIMILARITY", "0.85")
        )
        self._audit_full_response = (
            os.getenv("GREEN_CLAIM_AUDIT_FULL_RESPONSE", "false").lower() == "true"
        )

        # Services
        self._problematic_word_matcher = ProblematicWordMatcher(
            problematic_words=PROBLEMATIC_WORDS,
            similarity_threshold=self._problematic_word_threshold,
        )
        self._text_segmenter = TextSegmenter(chunk_size=self._chunk_size)
        self._response_comparator = ResponseComparator(
            statement_similarity_threshold=self._statement_similarity_threshold,
        )

        logger.debug(
            "GreenClaimService initialized "
            f"(model={self._model}, temperature={self._temperature}, "
            f"chunk_size={self._chunk_size}, "
            f"problematic_word_threshold={self._problematic_word_threshold}, "
            f"statement_similarity_threshold={self._statement_similarity_threshold})"
        )

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------

    async def run_green_claim_analysis(
        self,
        text: str,
        user_id: str,
        use_second_opinion: bool = False,
        include_formatted_report: bool = False,
    ) -> GreenClaimResponse:
        """Analyze text in a single LLM call."""
        start_time = datetime.now(UTC)
        logger.info(
            f"Starting green claim analysis (text_length={len(text)}, "
            f"use_second_opinion={use_second_opinion})"
        )

        try:
            llm_response = await self._analyze_text(
                text=text, use_second_opinion=use_second_opinion
            )
            problematic_words = await asyncio.to_thread(self._problematic_word_matcher.find, text)

            logger.info(f"Found {problematic_words.total_count} problematic words")

            result = GreenClaimResponse(
                green_claim_report=llm_response.green_claim_report,
                legal_statement=llm_response.legal_statement,
                guidelines=llm_response.guidelines,
                problematic_words=problematic_words,
            )

            if include_formatted_report:
                result.formatted_report = format_green_claim_as_markdown(result)

            # Audit successful analysis
            await self._log_successful_audit(
                user_id=user_id,
                analysis_type=ANALYSIS_TYPE_TEXT,
                start_time=start_time,
                input_metadata=self._build_text_metadata(text),
                result=result,
            )

            return result

        except Exception as e:
            # Audit failed analysis
            await self._log_failed_audit(
                user_id=user_id,
                analysis_type=ANALYSIS_TYPE_TEXT,
                start_time=start_time,
                input_metadata=self._build_text_metadata(text),
                error=str(e),
            )
            raise

    async def run_chunked_green_claim_analysis(
        self,
        text: str,
        user_id: str,
        use_second_opinion: bool = False,
        include_formatted_report: bool = False,
    ) -> GreenClaimResponse:
        """
        Analyze text by splitting it into chunks and merging results.
        """
        start_time = datetime.now(UTC)
        logger.info(
            f"Starting chunked analysis (length={len(text)}, "
            f"use_second_opinion={use_second_opinion})"
        )

        try:
            chunks = self._text_segmenter.segment(text)
            logger.info(f"Text segmented into {len(chunks)} chunks")

            if not chunks:
                logger.warning("No chunks generated from text")
                return self._empty_response()

            all_report_items = []
            legal_statement = ""
            guidelines = ""

            for index, chunk in enumerate(chunks, start=1):
                logger.info(f"Analyzing chunk {index}/{len(chunks)} ({len(chunk)} characters)")

                chunk_response = await self._analyze_text(
                    text=chunk, use_second_opinion=use_second_opinion
                )

                all_report_items.extend(chunk_response.green_claim_report)
                legal_statement = chunk_response.legal_statement
                guidelines = chunk_response.guidelines

            problematic_words = await asyncio.to_thread(self._problematic_word_matcher.find, text)

            logger.info(f"Chunked analysis complete ({len(all_report_items)} total report items)")

            result = GreenClaimResponse(
                green_claim_report=all_report_items,
                legal_statement=legal_statement,
                guidelines=guidelines,
                problematic_words=problematic_words,
            )

            # Add formatted report if requested
            if include_formatted_report:
                result.formatted_report = format_green_claim_as_markdown(result)

            # Audit successful analysis
            await self._log_successful_audit(
                user_id=user_id,
                analysis_type=ANALYSIS_TYPE_CHUNKED_TEXT,
                start_time=start_time,
                input_metadata=self._build_text_metadata(text, num_chunks=len(chunks)),
                result=result,
            )

            return result

        except Exception as e:
            # Audit failed analysis
            await self._log_failed_audit(
                user_id=user_id,
                analysis_type=ANALYSIS_TYPE_CHUNKED_TEXT,
                start_time=start_time,
                input_metadata=self._build_text_metadata(text),
                error=str(e),
            )
            raise

    async def run_green_claim_file_analysis(
        self,
        file_data: str,
        mime_type: str,
        filename: str,
        user_id: str,
        use_second_opinion: bool = False,
        include_formatted_report: bool = False,
    ) -> GreenClaimResponse:
        """
        Analyze an uploaded file (image or PDF) for green claims.

        Args:
            file_data: Base64-encoded data URI (e.g., 'data:image/jpeg;base64,...')
            use_second_opinion: Whether to run analysis twice and merge results

        Returns:
            GreenClaimResponse with empty problematic_words (only applicable to text)
        """
        start_time = datetime.now(UTC)
        logger.info(f"Starting green claim file analysis (use_second_opinion={use_second_opinion})")

        # Decoding and hashing the payload is CPU-bound on multi-MB uploads, so
        # it runs off the event loop and only once for both audit paths.
        input_metadata = await asyncio.to_thread(
            self._build_file_metadata, file_data, mime_type, filename
        )

        try:
            llm_response = await self._analyze_file(
                file_data=file_data,
                mime_type=mime_type,
                filename=filename,
                use_second_opinion=use_second_opinion,
            )

            # Problematic words are not analyzed for files
            logger.info("File analysis complete (problematic words not applicable)")

            result = GreenClaimResponse(
                green_claim_report=llm_response.green_claim_report,
                legal_statement=llm_response.legal_statement,
                guidelines=llm_response.guidelines,
                problematic_words=ProblematicWordResult(),
            )

            # Add formatted report if requested
            if include_formatted_report:
                result.formatted_report = format_green_claim_as_markdown(result)

            # Audit successful analysis
            await self._log_successful_audit(
                user_id=user_id,
                analysis_type=ANALYSIS_TYPE_FILE,
                start_time=start_time,
                input_metadata=input_metadata,
                result=result,
            )

            return result

        except Exception as e:
            # Audit failed analysis
            await self._log_failed_audit(
                user_id=user_id,
                analysis_type=ANALYSIS_TYPE_FILE,
                start_time=start_time,
                input_metadata=input_metadata,
                error=str(e),
            )
            raise

    # ---------------------------------------------------------------------
    # Core analysis helpers
    # ---------------------------------------------------------------------

    async def _analyze_text(
        self, text: str, use_second_opinion: bool = False
    ) -> GreenClaimLLMResponse:
        """
        Run LLM analysis on text input,
        optionally merging a second opinion.
        """
        if not use_second_opinion:
            primary_response = await self._call_llm(text)
            return self._parse_json_response(primary_response)

        logger.info("Running second opinion analysis in parallel")

        # Run both LLM calls in parallel
        primary_response, secondary_response = await asyncio.gather(
            self._call_llm(text),
            self._call_llm(text),
        )

        parsed_primary = self._parse_json_response(primary_response)
        parsed_secondary = self._parse_json_response(secondary_response)

        return self._response_comparator.merge_responses(parsed_primary, parsed_secondary)

    async def _analyze_file(
        self,
        file_data: str,
        mime_type: str,
        filename: str,
        use_second_opinion: bool = False,
    ) -> GreenClaimLLMResponse:
        """
        Run LLM analysis on file input,
        optionally merging a second opinion.
        """
        user_input = self._build_file_input(
            file_data=file_data,
            mime_type=mime_type,
            filename=filename,
        )

        if not use_second_opinion:
            primary_response = await self._call_llm(user_input)
            return self._parse_json_response(primary_response)

        logger.info("Running second opinion analysis in parallel")

        # Run both LLM calls in parallel
        primary_response, secondary_response = await asyncio.gather(
            self._call_llm(user_input),
            self._call_llm(user_input),
        )

        parsed_primary = self._parse_json_response(primary_response)
        parsed_secondary = self._parse_json_response(secondary_response)

        return self._response_comparator.merge_responses(parsed_primary, parsed_secondary)

    async def _call_llm(self, user_input: list[dict] | str) -> str:
        """Execute a single LLM chat completion call."""
        return await self._openai_repository.get_response_async(
            input=user_input,
            instructions=self._system_prompt,
            temperature=self._temperature,
            model=self._model,
        )

    def _build_file_input(self, file_data: str, mime_type: str, filename: str) -> list[dict]:
        """Construct LLM input messages for file analysis."""
        content = []
        # Determine instruction text based on file type
        if mime_type == "application/pdf":
            instruction_text = "Perform the analysis on the provided PDF document, including any images contained within it."
        elif mime_type and mime_type.startswith("image/"):
            instruction_text = "Perform the analysis on the provided image."
        else:
            instruction_text = "Perform the analysis on the provided file."

        content.append({"type": "input_text", "text": instruction_text})

        if mime_type.startswith("image/"):
            content.append({"type": "input_image", "image_url": file_data})
        else:
            content.append(
                {
                    "type": "input_file",
                    "filename": filename,
                    "file_data": file_data,
                }
            )

        return [{"role": "user", "content": content}]

    # ---------------------------------------------------------------------
    # Parsing & utilities
    # ---------------------------------------------------------------------

    def _load_system_prompt(self) -> str:
        """Load system prompt from markdown file."""
        prompt_path = Path(__file__).parent.parent / "prompts" / "analyze_green_claim.md"
        try:
            with open(prompt_path, encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            logger.error(f"System prompt file not found: {prompt_path}")
            raise
        except Exception as err:
            logger.error(f"Failed to load system prompt: {err}", exc_info=True)
            raise

    def _parse_json_response(self, response: str) -> GreenClaimLLMResponse:
        """Parse and validate JSON returned from the LLM."""
        try:
            parsed_json = json.loads(response)
        except json.JSONDecodeError as err:
            logger.error("Failed to parse LLM response as JSON", exc_info=True)
            raise ValueError(f"Invalid JSON response from OpenAI: {err}")

        try:
            return GreenClaimLLMResponse(**parsed_json)
        except ValidationError as err:
            logger.error("LLM response schema validation failed", exc_info=True)
            raise ValueError(f"OpenAI response does not match expected schema: {err}")

    def _empty_response(self) -> GreenClaimResponse:
        """Return a safe empty response."""
        return GreenClaimResponse(
            green_claim_report=[],
            legal_statement="",
            guidelines="",
            problematic_words=ProblematicWordResult(exact_matches=[], close_matches=[]),
        )

    # ---------------------------------------------------------------------
    # Audit helpers
    # ---------------------------------------------------------------------

    def _build_text_metadata(self, text: str, num_chunks: int | None = None) -> dict[str, Any]:
        """Build metadata for text input."""
        metadata = {
            "text_length": len(text),
            "text_hash": compute_text_hash(text),
        }
        if num_chunks is not None:
            metadata["num_chunks"] = num_chunks
        return metadata

    def _build_file_metadata(self, file_data: str, mime_type: str, filename: str) -> dict[str, Any]:
        """Build metadata for file input."""

        # Extract base64 data from data URI
        if "," in file_data:
            base64_data = file_data.split(",", 1)[1]
        else:
            base64_data = file_data

        file_bytes = base64.b64decode(base64_data)

        return {
            "filename": filename,
            "mime_type": mime_type,
            "file_size": len(file_bytes),
            "file_hash": compute_file_hash(file_bytes),
        }

    def _get_analysis_config(self) -> dict[str, Any]:
        """Get current analysis configuration."""
        return {
            "model": self._model,
            "temperature": self._temperature,
            "chunk_size": self._chunk_size,
            "problematic_word_threshold": self._problematic_word_threshold,
            "statement_similarity_threshold": self._statement_similarity_threshold,
        }

    async def _log_successful_audit(
        self,
        user_id: str,
        analysis_type: str,
        start_time: datetime,
        input_metadata: dict[str, Any],
        result: GreenClaimResponse,
    ) -> None:
        """Log a successful analysis to audit trail."""
        duration_ms = (datetime.now(UTC) - start_time).total_seconds() * 1000

        try:
            await self._audit_service.log_analysis(
                user_object_id=user_id,
                analysis_type=analysis_type,
                duration_ms=duration_ms,
                input_metadata=input_metadata,
                analysis_config=self._get_analysis_config(),
                output_summary=summarize_output(result),
                full_response=result.model_dump() if self._audit_full_response else None,
            )
        except Exception as e:
            logger.error(f"Failed to log audit: {e}", exc_info=True)

    async def _log_failed_audit(
        self,
        user_id: str,
        analysis_type: str,
        start_time: datetime,
        input_metadata: dict[str, Any],
        error: str,
    ) -> None:
        """Log a failed analysis to audit trail."""
        duration_ms = (datetime.now(UTC) - start_time).total_seconds() * 1000

        try:
            await self._audit_service.log_failed_analysis(
                user_object_id=user_id,
                analysis_type=analysis_type,
                duration_ms=duration_ms,
                input_metadata=input_metadata,
                analysis_config=self._get_analysis_config(),
                error=error,
            )
        except Exception as e:
            logger.error(f"Failed to log failed audit: {e}", exc_info=True)
