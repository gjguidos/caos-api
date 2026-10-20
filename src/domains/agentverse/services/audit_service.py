from __future__ import annotations

from typing import Any

from src.domains.agentverse.entities.report_log import (
    OperationLog,
    OperationLogCreate,
    OperationType,
)
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.services.db_service import DBService


class AuditService:
    """Service for tracking CRUD operations - who did what, with the payload.

    Agent turn reports used to live here too. They are now written by
    ``RunReportRepository`` as run reports, keyed on the orchestrator's
    ``run_id`` and owned by the requesting user.
    """

    def __init__(self, db_service: DBService):
        self.db_service = db_service

        AGENTVERSE_LOGGER.info(
            LogDomain.COMMAND_ROOM,
            "[🧾 AUDIT][⚙️ SERVICE] initialized",
            extra={
                "service": self.__class__.__name__,
                "has_db_service": db_service is not None,
            },
        )

    # ========================== OPERATION LOGS ==========================

    async def log_operation(
        self,
        operation_type: OperationType,
        user_object_id: str | None,
        payload: dict[str, Any],
    ) -> OperationLog:
        AGENTVERSE_LOGGER.info(
            LogDomain.COMMAND_ROOM,
            f"[🧾 AUDIT][📝 OPERATION] log_operation_start type={operation_type.value}",
            extra={
                "operation_type": operation_type.value,
                "payload_keys": list(payload.keys()) if isinstance(payload, dict) else [],
            },
        )

        try:
            log_data = OperationLogCreate(
                operation_type=operation_type,
                user_object_id=user_object_id,
                payload=payload,
            )

            created_log = await self.db_service.create_operation_log(log_data)
            created_id = created_log.get("_id")

            AGENTVERSE_LOGGER.info(
                LogDomain.COMMAND_ROOM,
                f"[🧾 AUDIT][✅ OPERATION] log_operation_success type={operation_type.value}",
                extra={
                    "operation_type": operation_type.value,
                    "log_id": str(created_id) if created_id is not None else None,
                },
            )

            return OperationLog(**created_log)

        except Exception as e:
            AGENTVERSE_LOGGER.error(
                LogDomain.COMMAND_ROOM,
                f"[🧾 AUDIT][💥 OPERATION] log_operation_failed type={operation_type.value}",
                extra={
                    "operation_type": operation_type.value,
                    "error_type": type(e).__name__,
                    "error": str(e),
                },
                exc_info=True,
            )
            raise

    async def get_operation_log(self, log_id: str) -> OperationLog | None:
        AGENTVERSE_LOGGER.info(
            LogDomain.COMMAND_ROOM,
            f"[🧾 AUDIT][🔎 OPERATION] get_operation_log_start id={log_id}",
            extra={
                "log_id": log_id,
            },
        )

        log_data = await self.db_service.get_operation_log_by_id(log_id)
        found = bool(log_data)

        AGENTVERSE_LOGGER.info(
            LogDomain.COMMAND_ROOM,
            f"[🧾 AUDIT][📦 OPERATION] get_operation_log_complete id={log_id}",
            extra={
                "log_id": log_id,
                "found": found,
            },
        )

        return OperationLog(**log_data) if log_data else None

    async def list_operation_logs(
        self,
        operation_type: OperationType | None = None,
        user_object_id: str | None = None,
        limit: int = 100,
        skip: int = 0,
    ) -> list[OperationLog]:
        filters = {}

        if operation_type:
            filters["operation_type"] = operation_type.value
        if user_object_id:
            filters["user_object_id"] = user_object_id

        AGENTVERSE_LOGGER.info(
            LogDomain.COMMAND_ROOM,
            "[🧾 AUDIT][📚 OPERATION] list_operation_logs_start",
            extra={
                "operation_type": operation_type.value if operation_type else None,
                "user_object_id": user_object_id,
                "limit": limit,
                "skip": skip,
                "filters": filters,
            },
        )

        logs_data = await self.db_service.list_operation_logs(
            filters=filters,
            limit=limit,
            skip=skip,
        )
        result = [OperationLog(**log_data) for log_data in logs_data]

        AGENTVERSE_LOGGER.info(
            LogDomain.COMMAND_ROOM,
            "[🧾 AUDIT][✅ OPERATION] list_operation_logs_success",
            extra={
                "operation_type": operation_type.value if operation_type else None,
                "user_object_id": user_object_id,
                "limit": limit,
                "skip": skip,
                "result_count": len(result),
            },
        )

        return result
