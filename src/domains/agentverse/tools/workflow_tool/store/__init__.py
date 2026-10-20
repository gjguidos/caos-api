from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from src.domains.agentverse.tools.workflow_tool.models import WorkflowRun

logger = logging.getLogger("agentverse.tools.workflow_tool.store")


class RunStore(Protocol):
    async def get(self, run_id: str) -> WorkflowRun | None: ...
    async def put(self, run: WorkflowRun) -> None: ...

    async def put_event_wait_index(
        self,
        *,
        event_type: str,
        correlation_id: str,
        run_id: str,
        step_id: str,
    ) -> None: ...

    async def get_event_wait_index(
        self,
        *,
        event_type: str,
        correlation_id: str,
    ) -> dict[str, str] | None: ...

    async def delete_event_wait_index(
        self,
        *,
        event_type: str,
        correlation_id: str,
    ) -> None: ...


class InMemoryRunStore:
    """
    Simple in-process workflow run store.

    Intended for local development and tests.
    Not suitable for multi-worker production deployments.
    """

    def __init__(self) -> None:
        self._db: dict[str, WorkflowRun] = {}
        self._event_index: dict[str, dict[str, str]] = {}

    def _event_key(self, event_type: str, correlation_id: str) -> str:
        return f"{event_type}:{correlation_id}"

    async def get(self, run_id: str) -> WorkflowRun | None:
        return self._db.get(run_id)

    async def put(self, run: WorkflowRun) -> None:
        self._db[run.run_id] = run

    async def put_event_wait_index(
        self,
        *,
        event_type: str,
        correlation_id: str,
        run_id: str,
        step_id: str,
    ) -> None:
        self._event_index[self._event_key(event_type, correlation_id)] = {
            "run_id": run_id,
            "step_id": step_id,
        }

    async def get_event_wait_index(
        self,
        *,
        event_type: str,
        correlation_id: str,
    ) -> dict[str, str] | None:
        return self._event_index.get(self._event_key(event_type, correlation_id))

    async def delete_event_wait_index(
        self,
        *,
        event_type: str,
        correlation_id: str,
    ) -> None:
        self._event_index.pop(
            self._event_key(event_type, correlation_id),
            None,
        )


class RedisLike(Protocol):
    async def get(self, key: str) -> Any: ...

    async def set(
        self,
        key: str,
        value: Any,
        expiration: int | None = None,
    ) -> None: ...

    async def delete(self, key: str) -> Any: ...


class RedisWorkflowRunStore:
    def __init__(
        self,
        *,
        repo: RedisLike,
        prefix: str = "wf",
        ttl_seconds: int | None = None,
    ) -> None:
        self.repo = repo
        self.prefix = prefix.strip(":") or "wf"
        self.ttl_seconds = ttl_seconds

    def _key(self, run_id: str) -> str:
        return f"{self.prefix}:run:{run_id}"

    def _event_key(self, event_type: str, correlation_id: str) -> str:
        return f"{self.prefix}:event:{event_type}:{correlation_id}"

    def _decode_raw(self, raw: Any) -> str | None:
        if raw is None:
            return None

        if isinstance(raw, bytes):
            return raw.decode("utf-8")

        if isinstance(raw, str):
            return raw

        logger.warning(
            "[WORKFLOW_STORE][REDIS][INVALID_RAW_TYPE] type=%s",
            type(raw).__name__,
        )
        return None

    def _load_json_object(self, raw: Any, *, key: str) -> dict[str, Any] | None:
        text = self._decode_raw(raw)
        if not text:
            return None

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning(
                "[WORKFLOW_STORE][REDIS][JSON_DECODE_FAILED] key=%s",
                key,
            )
            return None

        if not isinstance(data, dict):
            logger.warning(
                "[WORKFLOW_STORE][REDIS][INVALID_JSON_SHAPE] key=%s type=%s",
                key,
                type(data).__name__,
            )
            return None

        return data

    async def get(self, run_id: str) -> WorkflowRun | None:
        key = self._key(run_id)
        raw = await self.repo.get(key)

        data = self._load_json_object(raw, key=key)
        if data is None:
            return None

        try:
            return WorkflowRun.model_validate(data)
        except Exception:
            logger.warning(
                "[WORKFLOW_STORE][REDIS][RUN_VALIDATE_FAILED] key=%s",
                key,
                exc_info=True,
            )
            return None

    async def put(self, run: WorkflowRun) -> None:
        payload = json.dumps(run.model_dump(), ensure_ascii=False)

        await self.repo.set(
            self._key(run.run_id),
            payload,
            expiration=self.ttl_seconds,
        )

    async def put_event_wait_index(
        self,
        *,
        event_type: str,
        correlation_id: str,
        run_id: str,
        step_id: str,
    ) -> None:
        payload = json.dumps(
            {
                "run_id": run_id,
                "step_id": step_id,
            },
            ensure_ascii=False,
        )

        await self.repo.set(
            self._event_key(event_type, correlation_id),
            payload,
            expiration=self.ttl_seconds,
        )

    async def get_event_wait_index(
        self,
        *,
        event_type: str,
        correlation_id: str,
    ) -> dict[str, str] | None:
        key = self._event_key(event_type, correlation_id)
        raw = await self.repo.get(key)

        data = self._load_json_object(raw, key=key)
        if data is None:
            return None

        run_id = data.get("run_id")
        step_id = data.get("step_id")

        if not isinstance(run_id, str) or not isinstance(step_id, str):
            logger.warning(
                "[WORKFLOW_STORE][REDIS][INVALID_EVENT_INDEX] key=%s",
                key,
            )
            return None

        return {
            "run_id": run_id,
            "step_id": step_id,
        }

    async def delete_event_wait_index(
        self,
        *,
        event_type: str,
        correlation_id: str,
    ) -> None:
        await self.repo.delete(self._event_key(event_type, correlation_id))
