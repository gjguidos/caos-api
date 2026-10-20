from __future__ import annotations

import json
from typing import Any

from src.domains.agentverse.tools.web_crawler_tool.snapshots.models import (
    CrawlSnapshot,
)


class CrawlSnapshotStore:
    def __init__(
        self,
        *,
        repo: Any,
        session_id: str,
        user_id: str | None = None,
        ttl_seconds: int = 1800,
    ) -> None:
        self.repo = repo
        self.session_id = session_id
        self.user_id = user_id
        self.ttl_seconds = ttl_seconds

    def _key(
        self,
        acquisition_fingerprint: str,
    ) -> str:
        user_part = self.user_id or "anonymous"

        return (
            "crawler:snapshot:"
            f"{user_part}:"
            f"{self.session_id}:"
            f"{acquisition_fingerprint}"
        )

    async def get(
        self,
        acquisition_fingerprint: str,
    ) -> CrawlSnapshot | None:
        raw = await self.repo.get(
            self._key(acquisition_fingerprint)
        )

        if not raw:
            return None

        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")

        if isinstance(raw, str):
            return CrawlSnapshot.model_validate_json(raw)

        return CrawlSnapshot.model_validate(raw)

    async def put(
        self,
        snapshot: CrawlSnapshot,
    ) -> None:
        await self.repo.set(
            self._key(
                snapshot.acquisition_fingerprint
            ),
            snapshot.model_dump_json(),
            expiration=self.ttl_seconds,
        )

    async def delete(
        self,
        acquisition_fingerprint: str,
    ) -> None:
        await self.repo.delete(
            self._key(acquisition_fingerprint)
        )