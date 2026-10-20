# src/base/infrastructure/db/chromaDB/utils/openai_embedding_function.py
import asyncio

from langchain_core.embeddings import Embeddings


class OpenAIEmbeddingFunction(Embeddings):
    """
    Langchain-compatible embedding function wrapper for OpenAI repository.
    Provides both sync and async methods by wrapping async client calls.
    """

    def __init__(self, client):  # OpenAIRepository
        self.client = client

    def embed_query(self, text: str) -> list[float]:
        """Sync embedding for single query text"""
        return asyncio.run(self.aembed_query(text))

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Sync embedding for multiple documents"""
        return asyncio.run(self.aembed_documents(texts))

    async def aembed_query(self, text: str) -> list[float]:
        """Async embedding for single query text"""
        return (await self.client.get_embeddings_async([text]))[0]

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        """Async embedding for multiple documents"""
        return await self.client.get_embeddings_async(texts)
