from dependency_injector import containers, providers

from src.base.dependencies.containers.llm_container import LLMContainer
from src.base.infrastructure.db.chromaDB.chromadb_client import ChromaDBClient
from src.base.infrastructure.db.chromaDB.utils.create_embedding_function import (
    EmbeddingFunctionFactory,
)
from src.base.repositories.chromadb_repository import ChromaDBRepository


class ChromaDBContainer(containers.DeclarativeContainer):
    wiring_config = containers.WiringConfiguration(modules=["..."])

    llm_container = providers.Container(LLMContainer)

    # Resolve the actual embedding client by calling the embedding_for method
    embedding_client = providers.Callable(
        lambda container: container.embedding_for(),
        container=llm_container,  # <- delay actual call
    )

    embedding_function_factory = providers.Factory(
        EmbeddingFunctionFactory, client=embedding_client
    )

    embedding_function = providers.Factory(
        lambda factory: factory.create(), factory=embedding_function_factory
    )

    chromadb_client = providers.Singleton(
        lambda emb_func: ChromaDBClient(embedding_function=emb_func), embedding_function
    )

    chromadb_repository = providers.Singleton(ChromaDBRepository, chromadb_client=chromadb_client)
