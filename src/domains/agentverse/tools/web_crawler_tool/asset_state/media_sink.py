# asset_state/media_sink.py

from typing import Any

from .store import AssetStateStore
from .updater import persist_page_assets


class AssetStateMediaSink:
    def __init__(
        self,
        *,
        asset_store: AssetStateStore,
    ) -> None:
        self.asset_store = asset_store

    async def persist_media_items(
        self,
        *,
        run_id: str,
        source_page_url: str,
        media_items: list[dict[str, Any]],
    ) -> None:
        await persist_page_assets(
            asset_store=self.asset_store,
            run_id=run_id,
            page_url=source_page_url,
            reports={
                "media": {
                    "items": media_items,
                },
            },
        )