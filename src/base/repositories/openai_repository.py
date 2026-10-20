import logging

from src.base.infrastructure.ai.azure_openai_client import AzureOpenAIClient

logger = logging.getLogger(__name__)


class OpenAIRepository:
    """
    Thin, non-blocking wrapper around an OpenAIClient.
    Prefers the client's async methods; otherwise offloads sync methods
    to a worker thread so the event loop never blocks.
    """

    def __init__(self, openai_client: AzureOpenAIClient):
        self.client = openai_client

    # ---------------- Embeddings ----------------
    async def get_embeddings_async(self, texts: list[str]) -> list[list[float]]:
        """
        Async batch embeddings
        """
        try:
            return await self.client.get_embeddings_async(texts)
        except Exception as e:
            logger.error("Error retrieving embeddings: %s", e)
            raise

    # ----------------------------
    # Responses API
    # ----------------------------
    async def get_response_async(
        self,
        input: str | list[dict[str, str]],
        instructions: str | None = None,
        tools: list[dict] | None = None,
        temperature: float | None = 1,
        model: str | None = None,
    ) -> str:
        try:
            return await self.client.get_response_async(
                input, instructions, tools, temperature, model
            )
        except Exception as e:
            logger.error("Error retrieving async chat response: %s", e)
            raise

    # ---------------- Audio ----------------
    async def transcribe_audio_async(self, audio_file_path: str) -> str:
        """
        Async transcription.
        """
        try:
            return await self.client.transcribe_audio_async(audio_file_path)
        except Exception as e:
            logger.error("Failed to transcribe audio (async): %s", e)
            raise

    # ---------------- Images ----------------
    async def generate_image_async(
        self, prompt: str, n: int = 1, size: str = "1024x1024", **kwargs
    ) -> list[dict[str, str]]:
        """
        True async image generation.
        """
        try:
            return await self.client.generate_image_async(prompt=prompt, n=n, size=size, **kwargs)
        except Exception as e:
            logger.error("Failed to generate image (async): %s", e)
            raise
