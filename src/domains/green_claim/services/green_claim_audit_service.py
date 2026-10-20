import logging
from typing import Any

from src.domains.agentverse.services.db_service import DBService
from src.domains.green_claim.entities.audit_models import (
    ANALYSIS_TYPE_CHUNKED_TEXT,
    ANALYSIS_TYPE_FILE,
    ANALYSIS_TYPE_TEXT,
    AnalysisType,
    GreenClaimAudit,
    GreenClaimAuditCreate,
    GreenClaimAuditFilter,
)

logger = logging.getLogger(__name__)


class GreenClaimAuditService:
    """
    Handles auditing of green claim analyses.

    Responsibilities:
    - Log successful and failed analyses
    - Query audit logs
    - Compute usage statistics
    """

    def __init__(self, db_service: DBService):
        self._db_service = db_service

    # ------------------------------------------------------------------
    # Public write API
    # ------------------------------------------------------------------

    async def log_analysis(
        self,
        user_object_id: str,
        analysis_type: AnalysisType,
        duration_ms: float,
        input_metadata: dict[str, Any],
        analysis_config: dict[str, Any],
        output_summary: dict[str, Any],
        full_response: dict[str, Any] | None = None,
    ) -> GreenClaimAudit:
        """Log a successful analysis."""
        logger.info(f"[AUDIT] Logging {analysis_type} analysis (user={user_object_id})")

        audit = await self._create_audit(
            audit_data=self._build_success_audit(
                user_object_id=user_object_id,
                analysis_type=analysis_type,
                duration_ms=duration_ms,
                input_metadata=input_metadata,
                analysis_config=analysis_config,
                output_summary=output_summary,
                full_response=full_response,
            ),
        )

        logger.info(f"[AUDIT] Audit log created (id={audit.id})")
        return audit

    async def log_failed_analysis(
        self,
        user_object_id: str,
        analysis_type: AnalysisType,
        duration_ms: float,
        input_metadata: dict[str, Any],
        analysis_config: dict[str, Any],
        error: str,
    ) -> GreenClaimAudit:
        """Log a failed analysis."""
        logger.warning(f"[AUDIT] Logging failed {analysis_type} analysis (user={user_object_id})")

        audit = await self._create_audit(
            audit_data=self._build_failure_audit(
                user_object_id=user_object_id,
                analysis_type=analysis_type,
                duration_ms=duration_ms,
                input_metadata=input_metadata,
                analysis_config=analysis_config,
                error=error,
            ),
        )

        logger.info(f"[✅ AUDIT] Failed analysis audit created (id={audit.id})")
        return audit

    # ------------------------------------------------------------------
    # Public read API
    # ------------------------------------------------------------------

    async def get_audit_by_id(self, audit_id: str) -> GreenClaimAudit | None:
        """Retrieve a single audit log by ID."""
        result = await self._db_service.get_green_claim_audit_by_id(audit_id)
        return GreenClaimAudit(**result) if result else None

    async def list_audits(
        self,
        filters: GreenClaimAuditFilter | None = None,
        limit: int = 100,
        skip: int = 0,
    ) -> list[GreenClaimAudit]:
        """List audit logs with optional filtering and pagination."""
        query = self._build_query(filters)

        logger.info(f"[AUDIT] Querying audits: {query}")

        results = await self._db_service.list_green_claim_audits(
            filters=query, limit=limit, skip=skip
        )

        return [GreenClaimAudit(**audit) for audit in results]

    # ------------------------------------------------------------------
    # Audit creation helpers
    # ------------------------------------------------------------------

    async def _create_audit(
        self,
        audit_data: GreenClaimAuditCreate,
    ) -> GreenClaimAudit:
        created = await self._db_service.create_green_claim_audit(audit_data.model_dump())
        return GreenClaimAudit(**created)

    @staticmethod
    def _build_success_audit(
        user_object_id: str,
        analysis_type: AnalysisType,
        duration_ms: float,
        input_metadata: dict[str, Any],
        analysis_config: dict[str, Any],
        output_summary: dict[str, Any],
        full_response: dict[str, Any] | None,
    ) -> GreenClaimAuditCreate:
        return GreenClaimAuditCreate(
            user_object_id=user_object_id,
            analysis_type=analysis_type,
            duration_ms=duration_ms,
            input_metadata=input_metadata,
            analysis_config=analysis_config,
            output_summary=output_summary,
            full_response=full_response,
            success=True,
        )

    @staticmethod
    def _build_failure_audit(
        user_object_id: str,
        analysis_type: AnalysisType,
        duration_ms: float,
        input_metadata: dict[str, Any],
        analysis_config: dict[str, Any],
        error: str,
    ) -> GreenClaimAuditCreate:
        return GreenClaimAuditCreate(
            user_object_id=user_object_id,
            analysis_type=analysis_type,
            duration_ms=duration_ms,
            input_metadata=input_metadata,
            analysis_config=analysis_config,
            output_summary={},
            error=error,
            success=False,
        )

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_query(
        filters: GreenClaimAuditFilter | None,
    ) -> dict[str, Any]:
        if not filters:
            return {}

        query: dict[str, Any] = {}

        if filters.user_object_id:
            query["user_object_id"] = filters.user_object_id
        if filters.analysis_type:
            query["analysis_type"] = filters.analysis_type
        if filters.success is not None:
            query["success"] = filters.success
        if filters.start_date or filters.end_date:
            query["timestamp"] = {}
            if filters.start_date:
                query["timestamp"]["$gte"] = filters.start_date
            if filters.end_date:
                query["timestamp"]["$lte"] = filters.end_date

        return query

    async def _get_all_audits_for_user(
        self,
        user_object_id: str | None,
    ) -> list[GreenClaimAudit]:
        filters = GreenClaimAuditFilter(user_object_id=user_object_id)
        return await self.list_audits(filters=filters, limit=10_000)

    # ------------------------------------------------------------------
    # Statistics helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _count_success(audits: list[GreenClaimAudit]) -> int:
        return sum(1 for a in audits if a.success)

    @staticmethod
    def _count_failure(audits: list[GreenClaimAudit]) -> int:
        return sum(1 for a in audits if not a.success)

    @staticmethod
    def _success_rate(audits: list[GreenClaimAudit]) -> float:
        total = len(audits)
        return sum(1 for a in audits if a.success) / total if total > 0 else 0.0

    @staticmethod
    def _average_duration(audits: list[GreenClaimAudit]) -> float:
        if not audits:
            return 0.0
        return sum(a.duration_ms for a in audits) / len(audits)

    @staticmethod
    def _count_by_type(
        audits: list[GreenClaimAudit],
    ) -> dict[str, int]:
        return {
            ANALYSIS_TYPE_TEXT: sum(1 for a in audits if a.analysis_type == ANALYSIS_TYPE_TEXT),
            ANALYSIS_TYPE_CHUNKED_TEXT: sum(
                1 for a in audits if a.analysis_type == ANALYSIS_TYPE_CHUNKED_TEXT
            ),
            ANALYSIS_TYPE_FILE: sum(1 for a in audits if a.analysis_type == ANALYSIS_TYPE_FILE),
        }
