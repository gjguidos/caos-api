from dependency_injector import containers, providers

from src.base.config.config import settings
from src.base.infrastructure.db.mongoDB.mongo_client import MongoDBClient
from src.base.repositories.mongodb_repository import MongoDBRepository


class DatabaseContainer(containers.DeclarativeContainer):
    """
    Container for database-related dependencies.
    """

    # MongoDB client
    mongo_client = providers.Singleton(
        MongoDBClient,
        db_uri=settings.database.resolved_uri,
        db_name=settings.database.resolved_dbname,
    )

    # Generic repository factory
    mongodb_repository = providers.Factory(MongoDBRepository, client=mongo_client)
