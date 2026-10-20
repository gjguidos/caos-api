"""Storage for per-turn agent run reports.

Reports are diagnostic, high-volume and worthless once stale, so this is the
one collection in the repo that expires its own documents: each write stamps an
``expires_at`` and a TTL index removes the document once it passes. Setting
``run_report_ttl_days`` to 0 leaves ``expires_at`` unset, and Mongo never
removes documents whose TTL field is missing - so reports are kept forever.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from src.base.config.config import settings
from src.base.repositories.mongodb_repository import MongoDBRepository
from src.domains.agentverse.entities.run_report import AgentRunReport
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)

COLLECTION = "coll_agent_run_reports"

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200


class RunReportRepository:
    def __init__(self, mongodb_repository: MongoDBRepository):
        self.repository = mongodb_repository
        self._indexes_ready = False

    async def _ensure_indexes(self) -> None:
        if self._indexes_ready:
            return
        collection = await self.repository.get_collection(COLLECTION)
        await collection.create_index("run_id", unique=True)
        await collection.create_index("session_id")
        await collection.create_index("agent_id")
        await collection.create_index("created")
        await collection.create_index("expires_at", expireAfterSeconds=0)
        self._indexes_ready = True

    @staticmethod
    def _expires_at() -> datetime | None:
        days = int(settings.run_report.run_report_ttl_days or 0)
        if days <= 0:
            return None
        return datetime.now(UTC) + timedelta(days=days)

    async def store(
        self,
        *,
        run_id: str,
        report: dict[str, Any],
        session_id: str | None,
        agent_id: str | None,
        created_by: str | None,
    ) -> AgentRunReport:
        await self._ensure_indexes()

        doc = AgentRunReport(
            run_id=run_id,
            session_id=session_id,
            agent_id=agent_id,
            created_by=created_by,
            report=report,
            expires_at=self._expires_at(),
        )
        data = doc.model_dump(exclude_none=True, by_alias=True)

        # Keyed on run_id, so a retried turn overwrites rather than duplicates.
        await self.repository.upsert(
            query={"run_id": run_id},
            data=data,
            collection_name=COLLECTION,
        )

        AGENTVERSE_LOGGER.debug(
            LogDomain.COMMAND_ROOM,
            "[🧾 RUN REPORT][💾 STORE] run_report_stored",
            extra={"run_id": run_id, "session_id": session_id, "agent_id": agent_id},
        )
        return doc

    async def get(self, run_id: str, *, created_by: str | None = None) -> AgentRunReport | None:
        """Fetch one report. ``created_by`` narrows the query rather than the result,
        so a caller who does not own the run never has the document returned to them.
        """
        query: dict[str, Any] = {"run_id": run_id}
        if created_by is not None:
            query["created_by"] = created_by

        doc = await self.repository.find_one(query, COLLECTION)
        return AgentRunReport(**doc) if doc else None

    async def list_by_session(
        self,
        session_id: str,
        *,
        created_by: str | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
        skip: int = 0,
    ) -> list[AgentRunReport]:
        """Newest first. Sorted and capped in the query, not after the fetch.

        Reports are high-volume and each carries an arbitrarily large body, so
        a busy session must never be pulled into memory whole. The ``created``
        index backs the sort.
        """
        await self._ensure_indexes()

        query: dict[str, Any] = {"session_id": session_id}
        if created_by is not None:
            query["created_by"] = created_by

        collection = await self.repository.get_collection(COLLECTION)
        cursor = collection.find(query).sort("created", -1)

        if skip > 0:
            cursor = cursor.skip(skip)
        if limit > 0:
            cursor = cursor.limit(limit)

        docs = await cursor.to_list(length=limit if limit > 0 else None)
        return [AgentRunReport(**self.repository.client.sanitize_document(doc)) for doc in docs]
