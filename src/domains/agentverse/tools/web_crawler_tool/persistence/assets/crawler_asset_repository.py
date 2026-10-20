from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pymongo import UpdateOne
from pymongo.errors import OperationFailure

from src.domains.agentverse.tools.web_crawler_tool.asset_state.models import (
    AssetObservation,
    AssetState,
)

if TYPE_CHECKING:
    from src.base.repositories.mongodb_repository import (
        MongoDBRepository,
    )



class CrawlerAssetRepository:

    ASSET_STATES_COL = "coll_crawler_asset_states"

    ASSET_OBSERVATIONS_COL = "coll_crawler_asset_observations"



    def __init__(

        self,

        repository: MongoDBRepository,

    ) -> None:

        self.repository = repository

        self._asset_write_gate = asyncio.Semaphore(4)



    @staticmethod

    def _now() -> datetime:

        return datetime.now(UTC)



    async def ensure_asset_indexes(self) -> None:

        asset_states = await self.repository.get_collection(
            self.ASSET_STATES_COL
        )

        await asset_states.create_index(
            [("asset_key", 1)],
            unique=True,
            name="uq_crawler_asset_state_key",
        )

        observations = await self.repository.get_collection(

            self.ASSET_OBSERVATIONS_COL

        )



        await observations.create_index(

            [

                ("run_id", 1),

                ("asset_key", 1),

            ],

            name="ix_crawler_asset_observations_run_asset",

        )



        await observations.create_index(

            [("observation_key", 1)],

            unique=True,

            name="uq_crawler_asset_observation_key",

        )



        await observations.create_index(

            [("run_id", 1), ("observation_key", 1)],

            name="ix_crawler_asset_observations_run_order",

        )



    async def observe_assets(

        self,

        *,

        items: list[tuple[AssetState, AssetObservation]],

    ) -> dict[str, int]:

        async with self._asset_write_gate:

            for attempt in range(4):

                try:

                    return await self._observe_assets_once(

                        items=items,

                    )

                except OperationFailure as exc:

                    retryable = (

                        exc.code == 112

                        or exc.has_error_label(

                            "TransientTransactionError"

                        )

                    )



                    if not retryable or attempt == 3:

                        raise



                    await asyncio.sleep(

                        0.1 * (2 ** attempt)

                    )



        raise RuntimeError(

            "Asset persistence retry loop exited unexpectedly"

        )

    async def observe_asset(

        self,

        *,

        asset: AssetState,

        observation: AssetObservation,

    ) -> bool:

        """

        Record an asset observation exactly once.



        Returns:

            True  if this observation was new.

            False if this observation had already been recorded.

        """



        asset_states = await self.repository.get_collection(

            self.ASSET_STATES_COL

        )

        observations = await self.repository.get_collection(

            self.ASSET_OBSERVATIONS_COL

        )



        now = self._now()



        observation_doc = observation.model_dump(

            mode="python"

        )



        # The Motor collection exposes the underlying Mongo client.

        mongo_client = asset_states.database.client



        async with await mongo_client.start_session() as session:

            async with session.start_transaction():

                observation_result = (

                    await observations.update_one(

                        {

                            "observation_key": (

                                observation.observation_key

                            )

                        },

                        {

                            "$setOnInsert": observation_doc,

                            "$set": {

                                "last_seen_at": now,

                            },

                        },

                        upsert=True,

                        session=session,

                    )

                )



                is_new_observation = (

                    observation_result.upserted_id is not None

                )



                asset_update: dict[str, Any] = {

                    "$set": {

                        "url": asset.url,

                        "asset_type": asset.asset_type,

                        "subtype": asset.subtype,

                        "last_seen_at": now,

                        "is_internal": asset.is_internal,

                        "metadata": asset.metadata,

                    },

                    "$setOnInsert": {

                        "asset_key": asset.asset_key,

                        "first_seen_at": asset.first_seen_at,

                    },

                }



                if is_new_observation:

                    asset_update["$inc"] = {

                        "occurrence_count": 1,

                        "source_pages_count": 1,

                    }



                await asset_states.update_one(

                    {

                        "asset_key": asset.asset_key

                    },

                    asset_update,

                    upsert=True,

                    session=session,

                )



        return is_new_observation

    async def _observe_assets_once(

        self,

        *,

        items: list[tuple[AssetState, AssetObservation]],

    ) -> dict[str, int]:

        """

        Persist a bounded batch of asset observations atomically.



        Returns counts for newly inserted observations and assets.

        """



        if not items:

            return {

                "input_observations": 0,

                "new_observations": 0,

                "affected_assets": 0,

            }



        asset_states = await self.repository.get_collection(

            self.ASSET_STATES_COL

        )

        observations = await self.repository.get_collection(

            self.ASSET_OBSERVATIONS_COL

        )



        now = self._now()



        # Keep the latest asset representation for each asset key.

        assets_by_key: dict[str, AssetState] = {

            asset.asset_key: asset

            for asset, _ in items

        }



        observation_operations = []



        for _, observation in items:

            observation_operations.append(

                UpdateOne(

                    {

                        "observation_key": (

                            observation.observation_key

                        )

                    },

                    {

                        "$setOnInsert": observation.model_dump(

                            mode="python"

                        ),

                        "$set": {

                            "last_seen_at": now,

                        },

                    },

                    upsert=True,

                )

            )



        mongo_client = asset_states.database.client



        async with await mongo_client.start_session() as session:

            async with session.start_transaction():

                if observation_operations:

                    observation_result = await observations.bulk_write(

                        observation_operations,

                        ordered=False,

                        session=session,

                    )

                else:

                    return {

                        "input_observations": len(items),

                        "new_observations": 0,

                        "affected_assets": 0,

                    }



                new_indexes = set(

                    observation_result.upserted_ids.keys()

                )



                new_observation_keys = {

                    items[index][1].observation_key

                    for index in new_indexes

                }



                new_observations = [

                    observation

                    for _, observation in items

                    if observation.observation_key

                    in new_observation_keys

                ]



                increments = Counter(

                    observation.asset_key

                    for observation in new_observations

                )



                asset_operations = []



                new_asset_keys = {

                    observation.asset_key

                    for observation in new_observations

                }



                for asset_key in new_asset_keys:

                    asset = assets_by_key[asset_key]



                    update = {

                        "$set": {

                            "url": asset.url,

                            "asset_type": asset.asset_type,

                            "subtype": asset.subtype,

                            "last_seen_at": now,

                            "is_internal": asset.is_internal,

                            "metadata": asset.metadata,

                        },

                        "$setOnInsert": {

                            "asset_key": asset.asset_key,

                            "first_seen_at": asset.first_seen_at,

                        },

                        "$inc": {

                            "occurrence_count": increments[asset_key],

                            "source_pages_count": increments[asset_key],

                        },

                    }



                    asset_operations.append(

                        UpdateOne(

                            {"asset_key": asset_key},

                            update,

                            upsert=True,

                        )

                    )

            if asset_operations:

                await asset_states.bulk_write(

                    asset_operations,

                    ordered=False,

                    session=session,

                )



        return {

            "input_observations": len(items),

            "new_observations": len(new_observations),

            "affected_assets": len(

                {

                    observation.asset_key

                    for observation in new_observations

                }

            ),

        }

    async def get_asset_run_summary(

        self,

        *,

        run_id: str,

    ) -> dict[str, Any]:

        """Return aggregate asset reports for one crawl run.



        Asset states are cumulative across runs. Run membership is therefore

        resolved through the observation collection, which is keyed by

        ``run_id`` and ``observation_key``.



        The method deliberately returns counters only. Individual asset

        records remain in MongoDB and are not materialized into application

        memory.

        """

        observations = await self.repository.get_collection(

            self.ASSET_OBSERVATIONS_COL

        )



        pipeline = [

            {

                "$match": {

                    "run_id": run_id,

                },

            },

            {

                "$lookup": {

                    "from": self.ASSET_STATES_COL,

                    "localField": "asset_key",

                    "foreignField": "asset_key",

                    "as": "asset",

                },

            },

            {

                "$unwind": "$asset",

            },

            {

                "$group": {

                    "_id": {

                        "asset_type": "$asset.asset_type",

                        "subtype": {

                            "$ifNull": [

                                "$asset.subtype",

                                "unknown",

                            ],

                        },

                        "asset_key": "$asset.asset_key",

                    },

                    "occurrences": {

                        "$sum": 1,

                    },

                    "source_pages": {

                        "$addToSet": "$source_page_url",

                    },

                    "is_internal": {

                        "$first": "$asset.is_internal",

                    },

                },

            },

            {

                "$project": {

                    "_id": 0,

                    "asset_type": "$_id.asset_type",

                    "subtype": "$_id.subtype",

                    "unique_assets": {

                        "$literal": 1,

                    },

                    "occurrences": 1,

                    "source_page_count": {

                        "$size": "$source_pages",

                    },

                    "is_internal": 1,

                },

            },

            {

                "$group": {

                    "_id": {

                        "asset_type": "$asset_type",

                        "subtype": "$subtype",

                    },

                    "unique_assets": {

                        "$sum": "$unique_assets",

                    },

                    "occurrences": {

                        "$sum": "$occurrences",

                    },

                    "source_pages": {

                        "$sum": "$source_page_count",

                    },

                    "internal_assets": {

                        "$sum": {

                            "$cond": [

                                "$is_internal",

                                1,

                                0,

                            ],

                        },

                    },

                    "external_assets": {

                        "$sum": {

                            "$cond": [

                                "$is_internal",

                                0,

                                1,

                            ],

                        },

                    },

                },

            },

            {

                "$project": {

                    "_id": 0,

                    "asset_type": "$_id.asset_type",

                    "subtype": "$_id.subtype",

                    "unique_assets": 1,

                    "occurrences": 1,

                    "source_pages": 1,

                    "internal_assets": 1,

                    "external_assets": 1,

                },

            },

            {

                "$sort": {

                    "asset_type": 1,

                    "subtype": 1,

                },

            },

        ]



        rows = await observations.aggregate(

            pipeline

        ).to_list(length=None)



        page_count_pipeline = [

            {

                "$match": {

                    "run_id": run_id,

                },

            },

            {

                "$lookup": {

                    "from": self.ASSET_STATES_COL,

                    "localField": "asset_key",

                    "foreignField": "asset_key",

                    "as": "asset",

                },

            },

            {

                "$unwind": "$asset",

            },

            {

                "$group": {

                    "_id": {

                        "asset_type": "$asset.asset_type",

                        "source_page_url": "$source_page_url",

                    },

                },

            },

            {

                "$group": {

                    "_id": "$_id.asset_type",

                    "pages_with_assets": {

                        "$sum": 1,

                    },

                },

            },

        ]



        page_count_rows = await observations.aggregate(

            page_count_pipeline

        ).to_list(length=None)



        pages_with_assets = {

            row["_id"]: int(row.get("pages_with_assets") or 0)

            for row in page_count_rows

        }



        grouped: dict[str, dict[str, Any]] = {

            "media": {

                "contract_version": "crawler.media.site.v1",

                "total_media": 0,

                "unique_media": 0,

                "pages_with_media": pages_with_assets.get("media", 0),

                "image_count": 0,

                "video_count": 0,

                "iframe_count": 0,

                "background_image_count": 0,

                "external_media_count": 0,

                "unique_domains": [],

                "items": [],

            },

            "documents": {

                "contract_version": "crawler.documents.site.v1",

                "document_count": 0,

                "pdf_count": 0,

                "word_count": 0,

                "spreadsheet_count": 0,

                "presentation_count": 0,

                "internal_documents": 0,

                "external_documents": 0,

                "unique_domains": [],

                "items": [],

            },

        }



        for row in rows:

            asset_type = str(row.get("asset_type") or "")

            subtype = str(row.get("subtype") or "unknown").lower()

            unique_assets = int(row.get("unique_assets") or 0)

            occurrences = int(row.get("occurrences") or 0)

            internal_assets = int(row.get("internal_assets") or 0)

            external_assets = int(row.get("external_assets") or 0)



            report = grouped.get(asset_type)

            if report is None:

                continue



            if asset_type == "media":

                report["total_media"] += occurrences

                report["unique_media"] += unique_assets



                subtype_key = {

                    "image": "image_count",

                    "video": "video_count",

                    "iframe": "iframe_count",

                    "background": "background_image_count",

                    "background_image": "background_image_count",

                }.get(subtype)



                if subtype_key:

                    report[subtype_key] += unique_assets



                report["external_media_count"] += external_assets

            else:

                report["document_count"] += unique_assets

                report["internal_documents"] += internal_assets

                report["external_documents"] += external_assets



                subtype_key = {

                    "pdf": "pdf_count",

                    "word": "word_count",

                    "spreadsheet": "spreadsheet_count",

                    "presentation": "presentation_count",

                }.get(subtype)



                if subtype_key:

                    report[subtype_key] += unique_assets



        return grouped

    async def iter_asset_run_observations(

        self,

        *,

        run_id: str,

        batch_size: int = 1_000,

        asset_type: str | None = None,

    ) -> AsyncIterator[dict[str, Any]]:

        observations = await self.repository.get_collection(

            self.ASSET_OBSERVATIONS_COL,

        )



        pipeline = [

            {

                "$match": {

                    "run_id": run_id,

                },

            },

            {

                "$lookup": {

                    "from": self.ASSET_STATES_COL,

                    "localField": "asset_key",

                    "foreignField": "asset_key",

                    "as": "asset",

                },

            },

            {

                "$unwind": "$asset",

            },

            *(
                [{"$match": {"asset.asset_type": asset_type}}]
                if asset_type is not None
                else []
            ),

            {

                "$project": {

                    "_id": 0,

                    "run_id": 1,

                    "observation_key": 1,

                    "asset_key": 1,

                    "asset_type": "$asset.asset_type",

                    "subtype": "$asset.subtype",

                    "url": "$asset.url",

                    "source_page": "$source_page_url",

                    "is_internal": "$asset.is_internal",

                    "metadata": "$asset.metadata",

                },

            },

            {

                "$sort": {

                    "observation_key": 1,

                },

            },

        ]



        cursor = observations.aggregate(

            pipeline,

            allowDiskUse=True,

        )

        cursor.batch_size(batch_size)



        async for row in cursor:

            yield row
