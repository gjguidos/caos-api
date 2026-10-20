import logging
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection

from src.base.infrastructure.exceptions import MongoDBConnectionError

logger = logging.getLogger("mongodb client")


class MongoDBClient:
    def __init__(self, db_uri: str, db_name: str):
        self.client: AsyncIOMotorClient | None = None
        self.db = None
        self.db_uri = db_uri
        self.db_name = db_name

    async def connect(self) -> None:
        try:
            if not self.db_name or not self.db_name.strip():
                raise ValueError("Database name is empty. Set DB_NAME.")
            if self.client is None:
                self.client = AsyncIOMotorClient(
                    self.db_uri,
                    serverSelectionTimeoutMS=30000,  # Cosmos benefits from longer timeout
                    uuidRepresentation="standard",
                )
            await self.client.admin.command("ping")
            self.db = self.client[self.db_name]
            logger.info(f"Connected to database '{self.db_name}'")
        except Exception as e:
            logger.exception("Failed to connect to MongoDB")
            from traceback import format_exc

            raise MongoDBConnectionError(
                host=self.db_uri,
                error_details=f"{e.__class__.__name__}: {e}\n{format_exc()}",
                message="Database connection failed. Please check your MongoDB/Cosmos configuration and ensure the server is reachable.",
            ) from e

    async def disconnect(self) -> None:
        if self.client is not None:
            self.client.close()
            logger.info("Disconnected from MongoDB.")

    def get_collection(self, collection_name: str) -> AsyncIOMotorCollection:
        if self.db is None:
            raise MongoDBConnectionError(
                host=self.db_uri,
                message="Database connection not established. Call 'connect()' first.",
            )
        return self.db[collection_name]

    async def insert_one(self, document: dict[str, Any], collection: AsyncIOMotorCollection):
        try:
            result = await collection.insert_one(document)
            return result.inserted_id
        except Exception as e:
            logger.error(f"Error inserting document: {str(e)}")
            raise

    async def find(
        self, query: dict[str, Any], collection: AsyncIOMotorCollection
    ) -> list[dict[str, Any]]:
        try:
            documents = await collection.find(query).to_list(length=None)
            return [self.sanitize_document(doc) for doc in documents]
        except Exception as e:
            logger.error(f"Error finding documents: {str(e)}")
            raise

    async def find_one(
        self, query: dict[str, Any], collection: AsyncIOMotorCollection
    ) -> dict[str, Any] | None:
        try:
            document = await collection.find_one(query)
            return self.sanitize_document(document) if document else None
        except Exception as e:
            logger.error(f"Error finding document: {str(e)}")
            raise

    async def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        collection: AsyncIOMotorCollection,
        upsert: bool = False,
    ):
        try:
            result = await collection.update_one(query, update, upsert=upsert)
            return result  # ✅ return Motor result, not a set
        except Exception as e:
            logger.error(f"Error updating document: {str(e)}")
            raise

    async def replace_one(
        self,
        query: dict[str, Any],
        replacement: dict[str, Any],
        collection: AsyncIOMotorCollection,
        upsert: bool = False,
    ):
        try:
            result = await collection.replace_one(query, replacement, upsert=upsert)
            return result
        except Exception as e:
            logger.error(f"Error replacing document: {str(e)}")
            raise

    async def delete_one(self, query: dict[str, Any], collection: AsyncIOMotorCollection) -> int:
        try:
            result = await collection.delete_one(query)
            return result.deleted_count
        except Exception as e:
            logger.error(f"Error deleting document: {str(e)}")
            raise

    def sanitize_document(self, document: dict[str, Any]) -> dict[str, Any]:
        if document is None:
            return document
        if "_id" in document:
            # Cosmos may already use string ids; guard conversion
            try:
                from bson import ObjectId

                if isinstance(document["_id"], ObjectId):
                    document["_id"] = str(document["_id"])
            except Exception:
                pass
        return document
