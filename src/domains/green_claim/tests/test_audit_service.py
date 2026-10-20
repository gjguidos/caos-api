"""
Tests for Green Claim Audit Service

Tests audit logging functionality for text, chunked, and file analyses.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domains.green_claim.entities.audit_models import (
    ANALYSIS_TYPE_CHUNKED_TEXT,
    ANALYSIS_TYPE_FILE,
    ANALYSIS_TYPE_TEXT,
    GreenClaimAudit,
    GreenClaimAuditFilter,
)
from src.domains.green_claim.entities.green_claim_models import (
    GreenClaimResponse,
    ProblematicWordResult,
)
from src.domains.green_claim.services.green_claim_audit_service import (
    GreenClaimAuditService,
)
from src.domains.green_claim.utils.audit_utils import (
    compute_file_hash,
    compute_text_hash,
    summarize_output,
)


class TestAuditUtils:
    """Test audit utility functions."""

    def test_compute_text_hash(self):
        """Test text hashing produces consistent results."""
        text = "This is a test green claim"
        hash1 = compute_text_hash(text)
        hash2 = compute_text_hash(text)

        assert hash1 == hash2
        assert len(hash1) == 64  # SHA-256 hex string length
        assert isinstance(hash1, str)

    def test_compute_file_hash(self):
        """Test file hashing produces consistent results."""
        file_data = b"Mock PDF content"
        hash1 = compute_file_hash(file_data)
        hash2 = compute_file_hash(file_data)

        assert hash1 == hash2
        assert len(hash1) == 64
        assert isinstance(hash1, str)

    def test_summarize_output_with_violations(self):
        """Test output summary with violations."""
        from src.domains.green_claim.entities.green_claim_models import (
            GreenClaimReportItem,
            PrincipleViolation,
            ProblematicWordMatch,
        )

        response = GreenClaimResponse(
            green_claim_report=[
                GreenClaimReportItem(
                    statement="Our product is 100% eco-friendly",
                    principles_violated=[
                        PrincipleViolation(
                            principle="Principle 1",
                            explanation="Unsubstantiated claim",
                            guidelines_reference=["1. Accuracy"],
                            improvement_recommendation="Provide evidence",
                            confidence="strong",
                        ),
                        PrincipleViolation(
                            principle="Principle 2",
                            explanation="Vague wording",
                            guidelines_reference=["2. Clarity"],
                            improvement_recommendation="Be specific",
                            confidence="weak",
                        ),
                    ],
                )
            ],
            legal_statement="Legal disclaimer",
            guidelines="https://example.com",
            problematic_words=ProblematicWordResult(
                exact_matches=[
                    ProblematicWordMatch(
                        text="eco-friendly",
                        matched_against="eco-friendly",
                        confidence=1.0,
                        type="exact",
                        start=0,
                        end=12,
                    )
                ],
                close_matches=[],
            ),
        )

        summary = summarize_output(response)

        assert summary["green_claims_found"] == 1
        assert summary["total_violations"] == 2
        assert summary["strong_violations"] == 1
        assert summary["weak_violations"] == 1
        assert summary["problematic_words_exact"] == 1
        assert summary["problematic_words_close"] == 0
        assert summary["problematic_words_total"] == 1
        assert summary["has_violations"] is True
        assert summary["has_strong_confidence_violations"] is True

    def test_summarize_output_no_violations(self):
        """Test output summary with no violations."""
        response = GreenClaimResponse(
            green_claim_report=[],
            legal_statement="Legal disclaimer",
            guidelines="https://example.com",
            problematic_words=ProblematicWordResult(exact_matches=[], close_matches=[]),
        )

        summary = summarize_output(response)

        assert summary["green_claims_found"] == 0
        assert summary["total_violations"] == 0
        assert summary["strong_violations"] == 0
        assert summary["weak_violations"] == 0
        assert summary["problematic_words_total"] == 0
        assert summary["has_violations"] is False
        assert summary["has_strong_confidence_violations"] is False


class TestGreenClaimAuditService:
    """Test GreenClaimAuditService."""

    @pytest.fixture
    def mock_db_service(self):
        """Create a mock DB service."""
        db_service = MagicMock()
        db_service.create_green_claim_audit = AsyncMock(
            return_value={
                "_id": "test_audit_id",
                "user_object_id": "user123",
                "analysis_type": "text",
                "duration_ms": 100.0,
                "success": True,
            }
        )
        db_service.get_green_claim_audit_by_id = AsyncMock(
            return_value={
                "_id": "test_audit_id",
                "user_object_id": "user123",
                "analysis_type": "text",
                "duration_ms": 100.0,
                "success": True,
                "timestamp": datetime.now(timezone.utc),
                "created": datetime.now(timezone.utc),
                "modified": datetime.now(timezone.utc),
            }
        )
        db_service.list_green_claim_audits = AsyncMock(return_value=[])
        return db_service

    @pytest.fixture
    def audit_service(self, mock_db_service):
        """Create an audit service with mocked DB."""
        return GreenClaimAuditService(db_service=mock_db_service)

    @pytest.mark.asyncio
    async def test_log_successful_analysis(self, audit_service, mock_db_service):
        """Test logging a successful analysis."""
        audit = await audit_service.log_analysis(
            user_object_id="user123",
            analysis_type=ANALYSIS_TYPE_TEXT,
            duration_ms=123.45,
            input_metadata={"text_length": 100},
            analysis_config={"model": "gpt-4o"},
            output_summary={"green_claims_found": 1},
        )

        assert isinstance(audit, GreenClaimAudit)
        assert audit.id == "test_audit_id"
        mock_db_service.create_green_claim_audit.assert_called_once()

    @pytest.mark.asyncio
    async def test_log_failed_analysis(self, audit_service, mock_db_service):
        """Test logging a failed analysis."""
        audit = await audit_service.log_failed_analysis(
            user_object_id="user123",
            analysis_type=ANALYSIS_TYPE_FILE,
            duration_ms=50.0,
            input_metadata={"filename": "test.pdf"},
            analysis_config={"model": "gpt-4o"},
            error="Connection timeout",
        )

        assert isinstance(audit, GreenClaimAudit)
        assert audit.id == "test_audit_id"
        mock_db_service.create_green_claim_audit.assert_called_once()

        # Verify error was logged
        call_args = mock_db_service.create_green_claim_audit.call_args[0][0]
        assert call_args["error"] == "Connection timeout"
        assert call_args["success"] is False

    @pytest.mark.asyncio
    async def test_get_audit_by_id(self, audit_service, mock_db_service):
        """Test retrieving a specific audit."""
        audit = await audit_service.get_audit_by_id("test_audit_id")

        assert isinstance(audit, GreenClaimAudit)
        assert audit.id == "test_audit_id"
        mock_db_service.get_green_claim_audit_by_id.assert_called_once_with("test_audit_id")

    @pytest.mark.asyncio
    async def test_list_audits_with_filters(self, audit_service, mock_db_service):
        """Test listing audits with filters."""
        filters = GreenClaimAuditFilter(
            user_object_id="user123",
            analysis_type=ANALYSIS_TYPE_TEXT,
            success=True,
        )

        audits = await audit_service.list_audits(filters=filters, limit=50, skip=10)

        assert isinstance(audits, list)
        mock_db_service.list_green_claim_audits.assert_called_once()

        # Verify query was built correctly
        call_args = mock_db_service.list_green_claim_audits.call_args
        query = call_args[1]["filters"]
        assert query["user_object_id"] == "user123"
        assert query["analysis_type"] == "text"
        assert query["success"] is True

    @pytest.mark.asyncio
    async def test_build_query(self, audit_service):
        """Test query building from filters."""
        filters = GreenClaimAuditFilter(
            user_object_id="user123",
            analysis_type=ANALYSIS_TYPE_CHUNKED_TEXT,
            success=False,
            start_date=datetime(2026, 1, 1),
            end_date=datetime(2026, 1, 31),
        )

        query = audit_service._build_query(filters)

        assert query["user_object_id"] == "user123"
        assert query["analysis_type"] == "chunked_text"
        assert query["success"] is False
        assert "$gte" in query["timestamp"]
        assert "$lte" in query["timestamp"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
