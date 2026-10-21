
from dependency_injector import containers, providers

from src.base.infrastructure.db.chromaDB.chromadb_client import (
    ChromaDBClient,
)
from src.base.infrastructure.db.chromaDB.utils.create_embedding_function import (
    EmbeddingFunctionFactory,
)
from src.base.repositories.chromadb_repository import (
    ChromaDBRepository,
)


class ChromaDBContainer(containers.DeclarativeContainer):
    """ChromaDB infrastructure with shared embeddings."""

    wiring_config = containers.WiringConfiguration(
        modules=["..."]
    )

    llm_container = providers.DependenciesContainer()

    embedding_function_factory = providers.Factory(
        EmbeddingFunctionFactory,
        client=llm_container.embedding_for,
    )

    embedding_function = providers.Factory(
        lambda factory: factory.create(),
        factory=embedding_function_factory,
    )

    chromadb_client = providers.Singleton(
        ChromaDBClient,
        embedding_function=embedding_function,
    )

    chromadb_repository = providers.Singleton(
        ChromaDBRepository,
        chromadb_client=chromadb_client,
    )
