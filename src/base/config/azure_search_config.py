from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class AzureSearchSettings(BaseSettings):
    """
    Azure AI Search configuration for vector storage.
    """

    model_config = ConfigDict(frozen=True, env_file=".env", case_sensitive=False, extra="allow")

    # Azure Search endpoint and authentication
    azure_search_endpoint: str = ""
    azure_search_key: str = ""

    # Index configuration
    azure_search_index_prefix: str = "agentverse"
    azure_search_default_index: str = "default"

    # Vector configuration
    azure_search_vector_dimensions: int = 1536  # For text-embedding-ada-002
    azure_search_vector_algorithm: str = "hnsw"  # hnsw or exhaustiveKnn

    # Search configuration
    azure_search_top_k: int = 5
    azure_search_enable_semantic_ranking: bool = False

    @property
    def is_configured(self) -> bool:
        """Check if Azure Search is properly configured."""
        return bool(self.azure_search_endpoint and self.azure_search_key)

    def get_full_index_name(self, collection: str) -> str:
        """Get full index name with prefix."""
        if not collection:
            collection = self.azure_search_default_index
        return f"{self.azure_search_index_prefix}-{collection}".lower()
