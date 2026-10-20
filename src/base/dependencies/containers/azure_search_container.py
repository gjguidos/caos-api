import logging

from dependency_injector import containers, providers

from src.base.config.azure_search_config import AzureSearchSettings
from src.base.dependencies.containers.llm_container import LLMContainer
from src.base.infrastructure.db.azure_search.azure_search_client import AzureSearchClient
from src.base.repositories.azure_search_repository import AzureSearchRepository

logger = logging.getLogger(__name__)


class AzureSearchContainer(containers.DeclarativeContainer):
    """Dependency injection container for Azure AI Search."""

    wiring_config = containers.WiringConfiguration(modules=["..."])

    # Configuration
    settings = providers.Singleton(AzureSearchSettings)

    # LLM container for embedding function
    llm_container = providers.Container(LLMContainer)

    # Resolve embedding client
    embedding_client = providers.Callable(
        lambda container: container.embedding_for(), container=llm_container
    )

    # Create embedding function wrapper (same as ChromaDB)
    embedding_function = providers.Factory(
        lambda client: _create_embedding_function(client), client=embedding_client
    )

    # Azure Search client
    azure_search_client = providers.Singleton(
        lambda settings, emb_func: AzureSearchClient(
            endpoint=settings.azure_search_endpoint,
            key=settings.azure_search_key,
            embedding_function=emb_func,
            vector_dimensions=settings.azure_search_vector_dimensions,
            index_prefix=settings.azure_search_index_prefix,
        ),
        settings=settings,
        emb_func=embedding_function,
    )

    # Repository
    azure_search_repository = providers.Singleton(
        AzureSearchRepository, azure_search_client=azure_search_client
    )


def _create_embedding_function(client):
    """Create embedding function from Azure OpenAI client."""
    from src.base.infrastructure.db.chromaDB.utils.openai_embedding_function import (
        OpenAIEmbeddingFunction,
    )

    return OpenAIEmbeddingFunction(client)
