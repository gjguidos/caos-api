from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .models import AssetObservation, AssetState
from .store import AssetStateStore


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())

    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path or "/",
            parts.query,
            "",
        )
    )


def make_asset_key(
    *,
    asset_type: str,
    url: str,
) -> str:
    normalized = normalize_url(url)

    return (
        f"{asset_type}:"
        f"{sha256(normalized.encode('utf-8')).hexdigest()}"
    )

async def persist_page_assets(
    *,
    asset_store: AssetStateStore,
    run_id: str,
    page_url: str,
    reports: dict[str, Any],
) -> dict[str, int]:
    now = datetime.now(UTC)

    pending: dict[
        str,
        tuple[AssetState, AssetObservation],
    ] = {}

    def collect(
        *,
        items: list[dict[str, Any]],
        asset_type: str,
        url_field: str,
        subtype_field: str,
    ) -> None:
        for item in items:
            pair = _build_asset_pair(
                item=item,
                asset_type=asset_type,
                url_field=url_field,
                subtype_field=subtype_field,
                page_url=page_url,
                run_id=run_id,
                observed_at=now,
            )

            if pair is None:
                continue

            asset, observation = pair
            pending[observation.observation_key] = (
                asset,
                observation,
            )

    collect(
        items=reports.get("media", {}).get("items", []),
        asset_type="media",
        url_field="src",
        subtype_field="kind",
    )

    collect(
        items=reports.get("documents", {}).get("items", []),
        asset_type="document",
        url_field="url",
        subtype_field="document_type",
    )

    if not pending:
        return {
            "input_observations": 0,
            "new_observations": 0,
            "affected_assets": 0,
        }

    result = await asset_store.observe_assets(
        items=list(pending.values()),
    )

    return {
        "input_observations": result["input_observations"],
        "new_observations": result["new_observations"],
        "affected_assets": result.get(
            "affected_assets",
            result.get("new_assets", 0),
        ),
    }


async def persist_asset_inventory(
    *,
    asset_store: AssetStateStore,
    evidence,
    run_id: str,
    batch_size: int = 1_000,
) -> dict[str, int]:
    now = datetime.now(UTC)

    pending: dict[
        str,
        tuple[AssetState, AssetObservation],
    ] = {}

    totals = {
        "input_observations": 0,
        "new_observations": 0,
        "affected_assets": 0,
    }

    async def flush() -> None:
        if not pending:
            return

        result = await asset_store.observe_assets(
            items=list(pending.values()),
        )

        totals["input_observations"] += result[
            "input_observations"
        ]
        totals["new_observations"] += result[
            "new_observations"
        ]
        totals["affected_assets"] += result.get(
            "affected_assets",
            result.get("new_assets", 0),
        )

        pending.clear()

    for page in evidence.result.pages:
        page_url = page.final_url or page.url
        reports = page.reports or {}

        documents = reports.get("documents") or {}

        for item in documents.get("items") or []:
            pair = _build_asset_pair(
                item=item,
                asset_type="document",
                url_field="url",
                subtype_field="document_type",
                page_url=page_url,
                run_id=run_id,
                observed_at=now,
            )

            if pair is not None:
                asset, observation = pair
                pending[observation.observation_key] = (
                    asset,
                    observation,
                )

        media = reports.get("media") or {}

        for item in media.get("items") or []:
            pair = _build_asset_pair(
                item=item,
                asset_type="media",
                url_field="src",
                subtype_field="kind",
                page_url=page_url,
                run_id=run_id,
                observed_at=now,
            )

            if pair is not None:
                asset, observation = pair
                pending[observation.observation_key] = (
                    asset,
                    observation,
                )

        if len(pending) >= batch_size:
            await flush()

    await flush()

    return totals


def _build_asset_pair(
    *,
    item: dict[str, Any],
    asset_type: str,
    url_field: str,
    subtype_field: str,
    page_url: str,
    run_id: str,
    observed_at: datetime,
) -> tuple[AssetState, AssetObservation] | None:
    url = str(item.get(url_field) or "").strip()

    if not url:
        return None

    asset_key = make_asset_key(
        asset_type=asset_type,
        url=url,
    )

    source_page_url = normalize_url(page_url)

    observation = AssetObservation(
        observation_key=(
            f"{run_id}:{asset_key}:{source_page_url}"
        ),
        run_id=run_id,
        asset_key=asset_key,
        source_page_url=source_page_url,
        observed_at=observed_at,
    )

    asset = AssetState(
        asset_key=asset_key,
        url=url,
        asset_type=asset_type,
        subtype=item.get(subtype_field),
        first_seen_at=observed_at,
        last_seen_at=observed_at,
        is_internal=not bool(
            item.get("is_external", False)
        ),
        metadata=item,
    )

    return asset, observation