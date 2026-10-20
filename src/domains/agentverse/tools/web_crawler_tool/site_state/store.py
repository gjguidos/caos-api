from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.tools.web_crawler_tool.site_state.models import (
    SiteState,
)


class SiteStateStore:
    def __init__(
        self,
        *,
        repo: Any,
        user_id: str | None = None,
        ttl_seconds: int = 86_400,
    ) -> None:
        self.repo = repo
        self.user_id = user_id
        self.ttl_seconds = ttl_seconds

    def _key(
        self,
        domain: str,
    ) -> str:
        normalized_domain = (
            domain
            .strip()
            .lower()
        )

        user_part = (
            self.user_id
            or "anonymous"
        )

        return (
            "crawler:site-state:v2:"
            f"{user_part}:"
            f"{normalized_domain}"
        )

    async def get(
        self,
        domain: str,
    ) -> SiteState | None:
        key = self._key(
            domain
        )

        # --------------------------------------------------------------
        # Repository lookup.
        #
        # Use the raw Redis representation here so large SiteState
        # payloads are not decoded to Python strings on the event loop.
        # --------------------------------------------------------------
        repository_started = perf_counter()

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            (
                "[🕷️ WEB CRAWLER]"
                "[SITE_STATE]"
                "[GET_REPOSITORY_START] "
                f"domain={domain}"
            ),
        )

        raw = await self.repo.get_raw(
            key
        )

        repository_seconds = (
            perf_counter()
            - repository_started
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            (
                "[🕷️ WEB CRAWLER]"
                "[SITE_STATE]"
                "[GET_REPOSITORY_COMPLETE] "
                f"domain={domain} "
                f"found={bool(raw)} "
                f"seconds={repository_seconds:.3f}"
            ),
        )

        if not raw:
            return None

        # --------------------------------------------------------------
        # Deserialize / validate.
        #
        # Pydantic accepts JSON bytes directly, so there is no need to
        # decode a potentially large payload into a Python string first.
        #
        # Keep JSON parsing + model construction off the event loop.
        # --------------------------------------------------------------
        deserialize_started = perf_counter()

        raw_size = (
            len(raw)
            if isinstance(
                raw,
                str | bytes | bytearray,
            )
            else None
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            (
                "[🕷️ WEB CRAWLER]"
                "[SITE_STATE]"
                "[DESERIALIZE_START] "
                f"domain={domain} "
                f"raw_type={type(raw).__name__} "
                f"raw_size={raw_size}"
            ),
        )

        if isinstance(
            raw,
            str | bytes | bytearray,
        ):
            state = await asyncio.to_thread(
                SiteState.model_validate_json,
                raw,
            )
        else:
            state = await asyncio.to_thread(
                SiteState.model_validate,
                raw,
            )

        deserialize_seconds = (
            perf_counter()
            - deserialize_started
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            (
                "[🕷️ WEB CRAWLER]"
                "[SITE_STATE]"
                "[DESERIALIZE_COMPLETE] "
                f"domain={domain} "
                f"pages={len(state.pages)} "
                f"seconds={deserialize_seconds:.3f}"
            ),
        )

        return state

    async def put(
        self,
        state: SiteState,
    ) -> None:
        # --------------------------------------------------------------
        # Serialize off the event loop.
        # --------------------------------------------------------------
        serialize_started = perf_counter()

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            (
                "[🕷️ WEB CRAWLER]"
                "[SITE_STATE]"
                "[SERIALIZE_START] "
                f"domain={state.domain} "
                f"pages={len(state.pages)}"
            ),
        )

        payload, payload_bytes = await asyncio.to_thread(
            _serialize_state,
            state,
        )

        serialize_seconds = (
            perf_counter()
            - serialize_started
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            (
                "[🕷️ WEB CRAWLER]"
                "[SITE_STATE]"
                "[SERIALIZE_COMPLETE] "
                f"domain={state.domain} "
                f"pages={len(state.pages)} "
                f"bytes={payload_bytes} "
                f"seconds={serialize_seconds:.3f}"
            ),
        )

        await self.repo.set(
            self._key(
                state.domain
            ),
            payload,
            expiration=self.ttl_seconds,
        )

    async def delete(
        self,
        domain: str,
    ) -> None:
        await self.repo.delete(
            self._key(domain)
        )

def _serialize_state(
    state: SiteState,
) -> tuple[str, int]:
    payload = state.model_dump_json()
    payload_bytes = len(payload.encode("utf-8"))
    return payload, payload_bytes