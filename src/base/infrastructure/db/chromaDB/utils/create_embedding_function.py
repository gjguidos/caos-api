from src.base.infrastructure.ai.azure_openai_client import AzureOpenAIClient
from src.base.infrastructure.db.chromaDB.utils.openai_embedding_function import (
    OpenAIEmbeddingFunction,
)


class EmbeddingFunctionFactory:
    def __init__(self, client: AzureOpenAIClient):
        self.client = client

    def create(self) -> OpenAIEmbeddingFunction:
        return OpenAIEmbeddingFunction(self.client)
