import logging
from datetime import datetime, timezone
from typing import Any, Generic, TypeVar

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorCollection

from src.base.infrastructure.db.mongoDB.mongo_client import MongoDBClient

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=dict[str, Any])


class MongoDBRepository(Generic[T]):
    def __init__(self, client: MongoDBClient):
        self.client = client
        self._collections: dict[str, AsyncIOMotorCollection] = {}

    async def ensure_connected(self, collection_name: str) -> AsyncIOMotorCollection:
        if getattr(self.client, "db", None) is None:
            logger.debug("MongoDB client not connected, connecting now")
            await self.client.connect()
        if collection_name not in self._collections:
            self._collections[collection_name] = self.client.get_collection(collection_name)
        return self._collections[collection_name]

    async def create(self, data: T, collection_name: str) -> T:
        collection = await self.ensure_connected(collection_name)
        data_copy = data.copy()

        # Normalize _id None
        if data_copy.get("_id") is None:
            data_copy.pop("_id", None)

        now = datetime.now(timezone.utc)
        data_copy.setdefault("created", now)
        data_copy["modified"] = data_copy.get("modified", now)

        inserted_id = await self.client.insert_one(data_copy, collection)
        # Mirror the inserted id (ObjectId or string)
        if "_id" not in data_copy:
            data_copy["_id"] = str(inserted_id)
        return data_copy  # ✅ doc back; update the signature if you want str

    async def find_all(self, collection_name: str) -> list[T]:
        collection = await self.ensure_connected(collection_name)
        return await self.client.find({}, collection)

    async def find(self, query: dict[str, Any], collection_name: str) -> list[T]:
        collection = await self.ensure_connected(collection_name)
        return await self.client.find(query, collection)

    async def find_one(self, query: dict[str, Any], collection_name: str) -> T | None:
        collection = await self.ensure_connected(collection_name)
        return await self.client.find_one(query, collection)

    async def update(
        self,
        *,
        doc_id: str | None = None,
        data: dict[str, Any],
        collection_name: str,
        upsert: bool = False,
        query: dict[str, Any] | None = None,
    ) -> bool:
        collection = await self.ensure_connected(collection_name)

        if not query and not doc_id:
            raise ValueError("Must provide either doc_id or query")

        data.pop("_id", None)
        data["modified"] = datetime.now(timezone.utc)
        update_doc = {"$set": data}
        filter_query = query if query else {"_id": ObjectId(doc_id)}

        result = await self.client.update_one(filter_query, update_doc, collection, upsert=upsert)
        return bool(getattr(result, "matched_count", 0) > 0 or getattr(result, "upserted_id", None))

    async def upsert(
        self,
        *,
        doc_id: str | None = None,
        data: dict[str, Any],
        collection_name: str,
        query: dict[str, Any] | None = None,
    ) -> bool:
        # Upsert == update with upsert=True
        return await self.update(
            doc_id=doc_id,
            data=data,
            collection_name=collection_name,
            upsert=True,
            query=query,
        )

    async def delete(self, query: dict[str, Any], collection_name: str) -> bool:
        collection = await self.ensure_connected(collection_name)
        deleted = await self.client.delete_one(query, collection)
        return deleted > 0

    async def get_collection(self, collection_name: str) -> AsyncIOMotorCollection:
        return await self.ensure_connected(collection_name)
