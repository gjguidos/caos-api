from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, TypeVar

from pymongo import UpdateOne

from src.base.repositories.mongodb_repository import MongoDBRepository
from src.domains.agentverse.entities.agent import Agent
from src.domains.agentverse.entities.agent_request import AgentRequest
from src.domains.agentverse.entities.collection import CollectionDocument
from src.domains.agentverse.entities.report_log import OperationLogCreate
from src.domains.agentverse.events.socket_contract import LogLevel, LogStage
from src.domains.agentverse.exceptions import BlueprintConflictError
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.tools.web_crawler_tool.asset_state.models import (
    AssetObservation,
    AssetState,
)

T = TypeVar("T", bound=dict[str, Any])


class DBService:
    """Thin service around the Mongo repository living in app.state."""

    AGENTS_COL = "coll_agents"
    VDB_COLS_COL = "coll_vdb_collections"
    OPERATION_LOGS_COL = "coll_operation_logs"
    GREEN_CLAIM_AUDITS_COL = "coll_green_claim_audits"
    WORKFLOWS_COL = "coll_workflows"
    ASSET_STATES_COL = "coll_crawler_asset_states"
    ASSET_OBSERVATIONS_COL = "coll_crawler_asset_observations"

    def __init__(self, mongodb_repository: MongoDBRepository):
        self.repository = mongodb_repository

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    # --- validations --------------------------------------------------

    async def check_for_duplicates(
        self,
        socket_emitter,
        agent_config: AgentRequest,
    ) -> None:
        await socket_emitter.log(
            "Scanning Agentverse Existential Index [AEI] for prototype collisions...",
            level=LogLevel.INFO,
            stage=LogStage.REGISTER,
        )

        query = {
            "name": agent_config.name,
            "type": agent_config.type,
            "creator": agent_config.user_id,
        }

        AGENTVERSE_LOGGER.debug(
            LogDomain.EXISTENTIAL_INDEX,
            "[🧠 AEI][👯 CHECK] duplicate_scan_start",
            extra={
                "agent_name": agent_config.name,
                "agent_type": agent_config.type,
                "creator_user_id": agent_config.user_id,
            },
        )

        existing = await self.find_one(query, collection_name=self.AGENTS_COL)
        if existing:
            await socket_emitter.log(
                f"Conflict detected: EVA '{agent_config.name}' already exists for this creator/type.",
                level=LogLevel.ERROR,
                stage=LogStage.REGISTER,
            )

            AGENTVERSE_LOGGER.warning(
                LogDomain.EXISTENTIAL_INDEX,
                "[🧠 AEI][👯 CHECK] duplicate_scan_conflict",
                extra={
                    "agent_name": agent_config.name,
                    "agent_type": agent_config.type,
                    "creator_user_id": agent_config.user_id,
                },
            )
            raise BlueprintConflictError(field="name", value=agent_config.name)

        AGENTVERSE_LOGGER.info(
            LogDomain.EXISTENTIAL_INDEX,
            "[🧠 AEI][👯 CHECK] duplicate_scan_clear",
            extra={
                "agent_name": agent_config.name,
                "agent_type": agent_config.type,
                "creator_user_id": agent_config.user_id,
            },
        )

    # --- CRUD: agents -------------------------------------------------

    async def store_agent(self, socket_emitter, db_agent: Agent) -> dict[str, Any]:
        genesis_seal = db_agent.id[:6]
        now = self._now()

        AGENTVERSE_LOGGER.info(
            LogDomain.EXISTENTIAL_INDEX,
            "[🧠 AEI][💾 STORE] persist_start",
            extra={
                "agent_id": db_agent.id,
                "agent_name": db_agent.name,
                "seal": genesis_seal,
            },
        )

        doc = {
            **db_agent.model_dump(),
            "created_at": now,
            "updated_at": now,
        }

        created = await self.repository.create(doc, self.AGENTS_COL)

        AGENTVERSE_LOGGER.info(
            LogDomain.EXISTENTIAL_INDEX,
            "[🧠 AEI][💾 STORE] persist_complete",
            extra={
                "agent_id": db_agent.id,
                "agent_name": db_agent.name,
                "seal": genesis_seal,
            },
        )

        await socket_emitter.log(
            f"EVA unit '{db_agent.name}' inscribed in AEI with seal {genesis_seal}.",
            level=LogLevel.SUCCESS,
            stage=LogStage.REGISTER,
        )
        return created

    async def find_by_id(self, agent_id: str) -> T | None:
        return await self.find_one({"id": agent_id}, collection_name=self.AGENTS_COL)

    async def find_one(
        self,
        query: dict[str, Any],
        collection_name: str = AGENTS_COL,
    ) -> T | None:
        AGENTVERSE_LOGGER.debug(
            LogDomain.EXISTENTIAL_INDEX,
            "[🧠 AEI][🔍 FIND] find_one",
            extra={
                "collection": collection_name,
                "query": query,
            },
        )
        return await self.repository.find_one(query, collection_name)
    
    async def find_many(
        self,
        query: dict[str, Any],
        collection_name: str,
    ) -> list[dict[str, Any]]:
        collection = await self.repository.get_collection(
            collection_name,
        )

        return await collection.find(query).to_list(
            length=None,
        )

    async def find_all(
        self,
        query: dict[str, Any] | None = None,
        collection_name: str = AGENTS_COL,
    ) -> list[T]:
        AGENTVERSE_LOGGER.debug(
            LogDomain.EXISTENTIAL_INDEX,
            "[🧠 AEI][🔍 FIND] find_all",
            extra={
                "collection": collection_name,
                "query": query or {},
            },
        )

        if query:
            return await self.repository.find(query, collection_name)
        return await self.repository.find_all(collection_name)

    async def update_agent(
        self,
        agent_id: str,
        update_data: dict[str, Any],
        collection_name: str = AGENTS_COL,
    ) -> dict[str, Any] | None:
        existing = await self.find_one({"id": agent_id}, collection_name)
        if not existing:
            AGENTVERSE_LOGGER.warning(
                LogDomain.EXISTENTIAL_INDEX,
                "[🧠 AEI][🧬 MUTATE] update_missing",
                extra={
                    "agent_id": agent_id,
                    "collection": collection_name,
                },
            )
            raise ValueError(f"Agent with ID '{agent_id}' not found")

        mutable_fields: Iterable[str] = (
            "name",
            "system_name",
            "type",
            "prompt",
            "personality_key",
            "collections",
            "metadata",
            "llm_type",
            "audio_type",
            "image_type",
            "video_type",
            "db_type",
            "cache_type",
            "knowledge_db_type",
            "embedding_type",
            "messaging_type",
            # Agent configuration fields
            "role_name",
            "tools",
            "workflows",
            "rule_pack_ids",
            # Group-based visibility fields
            "visibility",
            "whitelisted_groups",
        )
        safe_payload = {k: v for k, v in update_data.items() if k in mutable_fields}
        if not safe_payload:
            AGENTVERSE_LOGGER.debug(
                LogDomain.EXISTENTIAL_INDEX,
                "[🧠 AEI][🧬 MUTATE] update_noop",
                extra={"agent_id": agent_id},
            )
            return existing

        safe_payload["updated_at"] = self._now()

        AGENTVERSE_LOGGER.info(
            LogDomain.EXISTENTIAL_INDEX,
            "[🧠 AEI][🧬 MUTATE] update_apply",
            extra={
                "agent_id": agent_id,
                "updated_fields": list(safe_payload.keys()),
            },
        )

        await self.repository.update(
            query={"id": agent_id},
            data=safe_payload,
            collection_name=collection_name,
        )
        return await self.find_one({"id": agent_id}, collection_name)

    async def delete_chat_agent(
        self,
        agent_id: str,
        collection_name: str = AGENTS_COL,
    ) -> bool:
        AGENTVERSE_LOGGER.info(
            LogDomain.EXISTENTIAL_INDEX,
            "[🧠 AEI][🗑️ DELETE] delete_start",
            extra={
                "agent_id": agent_id,
                "collection": collection_name,
            },
        )

        existing = await self.find_one({"id": agent_id}, collection_name)
        if not existing:
            AGENTVERSE_LOGGER.warning(
                LogDomain.EXISTENTIAL_INDEX,
                "[🧠 AEI][🗑️ DELETE] delete_missing",
                extra={
                    "agent_id": agent_id,
                    "collection": collection_name,
                },
            )
            return False

        ok = await self.repository.delete({"id": agent_id}, collection_name)

        AGENTVERSE_LOGGER.info(
            LogDomain.EXISTENTIAL_INDEX,
            "[🧠 AEI][🗑️ DELETE] delete_complete" if ok else "[🧠 AEI][🗑️ DELETE] delete_noop",
            extra={
                "agent_id": agent_id,
                "collection": collection_name,
            },
        )
        return bool(ok)

    # --- CRUD: vector collections ------------------------------------

    async def upsert_collection(self, col_doc: CollectionDocument) -> CollectionDocument:
        now = self._now()
        data = col_doc.dict()
        data["updated_at"] = now

        q = {"name": data["name"]}
        existing = await self.find_one(q, collection_name=self.VDB_COLS_COL)

        AGENTVERSE_LOGGER.info(
            LogDomain.EXISTENTIAL_INDEX,
            "[🧠 AEI][📚 VDB] upsert_start",
            extra={
                "collection_name": data["name"],
                "agent_id": data.get("agent_id"),
                "existing": existing is not None,
            },
        )

        if existing:
            await self.repository.update(query=q, data=data, collection_name=self.VDB_COLS_COL)
            updated = await self.find_one(q, collection_name=self.VDB_COLS_COL)
        else:
            data["created_at"] = now
            await self.repository.create(data, self.VDB_COLS_COL)
            updated = data

        AGENTVERSE_LOGGER.info(
            LogDomain.EXISTENTIAL_INDEX,
            "[🧠 AEI][📚 VDB] upsert_complete",
            extra={
                "collection_name": data["name"],
                "agent_id": data.get("agent_id"),
            },
        )

        return CollectionDocument(**updated)

    async def find_collection(
        self,
        collection_name: str,
        agent_id: str,
    ) -> CollectionDocument | None:
        q = {"name": collection_name, "agent_id": agent_id}
        res = await self.find_one(q, collection_name=self.VDB_COLS_COL)
        return CollectionDocument(**res) if res else None

    async def find_collection_by_name(self, collection_name: str) -> CollectionDocument | None:
        """Look up a collection by name alone, the key `upsert_collection` writes on."""
        res = await self.find_one({"name": collection_name}, collection_name=self.VDB_COLS_COL)
        return CollectionDocument(**res) if res else None

    async def find_collections(self, user_id: str | None = None) -> list[dict[str, Any]]:
        q = (
            {"visibility": "public"}
            if not user_id
            else {"$or": [{"owner_id": user_id}, {"visibility": "public"}]}
        )

        AGENTVERSE_LOGGER.debug(
            LogDomain.EXISTENTIAL_INDEX,
            "[🧠 AEI][📚 VDB] list_query",
            extra={
                "user_id": user_id,
                "query": q,
            },
        )

        return await self.repository.find(q, self.VDB_COLS_COL)

    async def add_collection_to_agent(self, agent_id: str, collection_name: str) -> None:
        agent = await self.find_by_id(agent_id)
        if not agent:
            AGENTVERSE_LOGGER.warning(
                LogDomain.EXISTENTIAL_INDEX,
                "[🧠 AEI][📚 VDB] add_collection_missing_agent",
                extra={
                    "agent_id": agent_id,
                    "collection_name": collection_name,
                },
            )
            raise ValueError(f"Agent '{agent_id}' not found")

        current = list(agent.get("collections") or [])
        if collection_name not in current:
            current.append(collection_name)

            AGENTVERSE_LOGGER.info(
                LogDomain.EXISTENTIAL_INDEX,
                "[🧠 AEI][📚 VDB] collection_link_apply",
                extra={
                    "agent_id": agent_id,
                    "collection_name": collection_name,
                },
            )

            await self.update_agent(agent_id, {"collections": current})

    # --- CRUD: operation logs -----------------------------------------

    async def create_operation_log(self, log_data: OperationLogCreate) -> dict[str, Any]:
        doc = {**log_data.model_dump()}

        AGENTVERSE_LOGGER.info(
            LogDomain.EXISTENTIAL_INDEX,
            "[🧠 AEI][📝 AUDIT] operation_log_create",
            extra={
                "operation_type": str(log_data.operation_type),
            },
        )

        created = await self.repository.create(doc, self.OPERATION_LOGS_COL)
        return self.repository.client.sanitize_document(created)

    async def get_operation_log_by_id(self, log_id: str) -> dict[str, Any] | None:
        return await self.repository.find_one({"_id": log_id}, self.OPERATION_LOGS_COL)

    async def list_operation_logs(
        self,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        skip: int = 0,
    ) -> list[dict[str, Any]]:
        query = filters or {}

        AGENTVERSE_LOGGER.debug(
            LogDomain.EXISTENTIAL_INDEX,
            "[🧠 AEI][📝 AUDIT] operation_log_list",
            extra={
                "filters": query,
                "limit": limit,
                "skip": skip,
            },
        )

        collection = await self.repository.get_collection(self.OPERATION_LOGS_COL)
        cursor = collection.find(query).sort("created_at", -1)

        if skip > 0:
            cursor = cursor.skip(skip)
        if limit > 0:
            cursor = cursor.limit(limit)

        results = await cursor.to_list(length=limit if limit > 0 else None)
        return [self.repository.client.sanitize_document(doc) for doc in results]

    # --- CRUD: green claim audits ------------------------------------

    async def create_green_claim_audit(self, audit_data: dict[str, Any]) -> dict[str, Any]:
        AGENTVERSE_LOGGER.info(
            LogDomain.EXISTENTIAL_INDEX,
            "[🧠 AEI][🌿 AUDIT] green_claim_audit_create",
            extra={"analysis_type": audit_data.get("analysis_type")},
        )

        created = await self.repository.create(audit_data, self.GREEN_CLAIM_AUDITS_COL)
        return self.repository.client.sanitize_document(created)

    async def get_green_claim_audit_by_id(self, audit_id: str) -> dict[str, Any] | None:
        return await self.repository.find_one({"_id": audit_id}, self.GREEN_CLAIM_AUDITS_COL)

    async def list_green_claim_audits(
        self,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        skip: int = 0,
    ) -> list[dict[str, Any]]:
        query = filters or {}

        AGENTVERSE_LOGGER.debug(
            LogDomain.EXISTENTIAL_INDEX,
            "[🧠 AEI][🌿 AUDIT] green_claim_audit_list",
            extra={
                "filters": query,
                "limit": limit,
                "skip": skip,
            },
        )

        collection = await self.repository.get_collection(self.GREEN_CLAIM_AUDITS_COL)
        cursor = collection.find(query).sort("timestamp", -1)

        if skip > 0:
            cursor = cursor.skip(skip)
        if limit > 0:
            cursor = cursor.limit(limit)

        results = await cursor.to_list(length=limit if limit > 0 else None)
        return [self.repository.client.sanitize_document(doc) for doc in results]


    async def ensure_asset_indexes(self) -> None:
        asset_states = await self.repository.get_collection(
            self.ASSET_STATES_COL
        )
        observations = await self.repository.get_collection(
            self.ASSET_OBSERVATIONS_COL
        )

        await asset_states.create_index(
            [("asset_key", 1)],
            unique=True,
            name="uq_crawler_asset_key",
        )

        await observations.create_index(
            [("observation_key", 1)],
            unique=True,
            name="uq_crawler_asset_observation_key",
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
    
    async def observe_assets(
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
                observation_result = (
                    await observations.bulk_write(
                        observation_operations,
                        ordered=False,
                        session=session,
                    )
                )

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

                for asset_key, asset in assets_by_key.items():
                    update: dict[str, Any] = {
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

                    increment = increments.get(asset_key, 0)

                    if increment:
                        update["$inc"] = {
                            "occurrence_count": increment,
                            "source_pages_count": increment,
                        }

                    asset_operations.append(
                        UpdateOne(
                            {"asset_key": asset_key},
                            update,
                            upsert=True,
                        )
                    )

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
    
    async def get_asset_run_observations(
        self,
        *,
        run_id: str,
    ) -> list[dict[str, Any]]:
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
        ]

        return await observations.aggregate(
            pipeline,
        ).to_list(length=None)
