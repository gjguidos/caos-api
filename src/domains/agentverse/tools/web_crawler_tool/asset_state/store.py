from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol

from src.domains.agentverse.tools.web_crawler_tool.asset_state.models import (
    AssetObservation,
    AssetState,
)


class AssetStateDatabase(Protocol):
    ASSET_STATES_COL: str

    async def ensure_asset_indexes(self) -> None:
        ...

    async def observe_asset(
        self,
        *,
        asset: AssetState,
        observation: AssetObservation,
    ) -> bool:
        ...

    async def observe_assets(
        self,
        *,
        items: list[tuple[AssetState, AssetObservation]],
    ) -> dict[str, int]:
        ...

    async def iter_asset_run_observations(
        self,
        *,
        run_id: str,
        batch_size: int = 1_000,
        asset_type: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        ...

    async def get_asset_run_summary(
        self,
        *,
        run_id: str,
    ) -> dict[str, Any]:
        ...

    async def find_one(
        self,
        query: dict[str, Any],
        collection_name: str,
    ) -> dict[str, Any] | None:
        ...

    async def find_many(
        self,
        query: dict[str, Any],
        collection_name: str,
    ) -> list[dict[str, Any]]:
        ...

    async def get_asset_run_observations(
        self,
        *,
        run_id: str,
    ) -> list[dict[str, Any]]:
        ...


class AssetStateStore:
    def __init__(
        self,
        *,
        db_service: AssetStateDatabase,
    ) -> None:
        self.db_service = db_service

    async def ensure_indexes(self) -> None:
        await self.db_service.ensure_asset_indexes()

    async def observe_asset(
        self,
        *,
        asset: AssetState,
        observation: AssetObservation,
    ) -> bool:
        return await self.db_service.observe_asset(
            asset=asset,
            observation=observation,
        )
    
    async def iter_run_observations(
        self,
        *,
        run_id: str,
        batch_size: int = 1_000,
        asset_type: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        async for item in self.db_service.iter_asset_run_observations(
            run_id=run_id,
            batch_size=batch_size,
            asset_type=asset_type,
        ):
            yield item

    async def get(
        self,
        asset_key: str,
    ) -> AssetState | None:
        raw = await self.db_service.find_one(
            {"asset_key": asset_key},
            collection_name=(
                self.db_service.ASSET_STATES_COL
            ),
        )

        if raw is None:
            return None

        return AssetState.model_validate(raw)
    
    async def get_run_observations(
        self,
        *,
        run_id: str,
    ) -> list[dict[str, Any]]:
        return await self.db_service.get_asset_run_observations(
            run_id=run_id,
        )
    
    async def observe_assets(
        self,
        *,
        items: list[tuple[AssetState, AssetObservation]],
    ) -> dict[str, int]:
        return await self.db_service.observe_assets(
            items=items,
        )
    
    async def get_run_summary(
        self,
        *,
        run_id: str,
    ) -> dict[str, Any]:
        return await self.db_service.get_asset_run_summary(
            run_id=run_id,
        )
