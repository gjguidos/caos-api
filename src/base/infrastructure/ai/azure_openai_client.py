import asyncio
import logging
import time
from pathlib import Path

from openai import AsyncOpenAI

from src.base.infrastructure.ai.llm_usage import record_llm_call
from src.base.utils.message_logger import MessageLogger

logger = logging.getLogger(__name__)


class AzureOpenAIClient:
    """
    Non-blocking Azure OpenAI client.
    - Prefer the async methods from async code paths (WS/orchestrator).
    - Sync wrappers hop to a helper thread to avoid blocking the event loop.
    """

    provider: str = "openai"

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        chat_deployment: str,
        completion_deployment: str,
        whispering_deployment: str,
        embedding_deployment: str | None = None,
        image_deployment: str | None = None,
        request_timeout: float = 180.0,
    ):
        if not api_key:
            raise ValueError("Missing Azure OpenAI API key")

        self.async_client = AsyncOpenAI(
            api_key=api_key,
            base_url=endpoint,
            timeout=request_timeout,
        )

        self.endpoint = endpoint
        self.chat_deployment = chat_deployment
        self.completion_deployment = completion_deployment
        self.embedding_deployment = embedding_deployment
        self.whispering_deployment = whispering_deployment
        self.image_deployment = image_deployment

        # Initialize message logger if _write_request_and_response_to_file is True
        # OBS. This is only used for local development and debugging.
        self._write_request_and_response_to_file = False
        if self._write_request_and_response_to_file:
            self.message_logger = MessageLogger()

        logger.info(
            f"🔐 AzureOpenAIClient initialized — endpoint: {endpoint}, chat deployment: {chat_deployment}"
        )

    # ----------------------------
    # Responses API
    # ----------------------------
    async def get_response_async(
        self,
        input: str | list[dict[str, str]],
        instructions: str | None = None,
        tools: list[dict] | None = None,
        temperature: float | None = 0.7,
        model: str | None = None,
    ) -> str:
        deployment = model or self.chat_deployment
        logger.debug(f"[Response/async] Invoking model: {deployment}")

        started = time.perf_counter()
        response = await self.async_client.responses.create(
            input=input,
            instructions=instructions,
            tools=tools,
            temperature=temperature,
            model=deployment,
            store=False,  # do not store in their server as we are handling history ourselves
        )

        # The caller contract stays `-> str`; usage and the model that actually
        # served the call would otherwise be discarded here, so hand them to the
        # side-channel instead.
        record_llm_call(
            provider=self.provider,
            model=getattr(response, "model", None) or deployment,
            usage=getattr(response, "usage", None),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

        # Log the response to files if enabled
        if self._write_request_and_response_to_file:
            self.message_logger.write_messages_to_file(input, self.chat_deployment)
            self.message_logger.write_messages_to_text_file(input, self.chat_deployment)
            self.message_logger.log_response(response.output_text, self.chat_deployment)

        return response.output_text

    # ----------------------------
    # Embeddings (batch-friendly)
    # ----------------------------
    async def get_embeddings_async(
        self, texts: list[str], model: str | None = None
    ) -> list[list[float]]:
        """
        Calls embeddings once with a batch of texts (non-blocking).
        """
        deployment = model or self.embedding_deployment
        if not deployment:
            raise ValueError("Embedding deployment name is required")
        resp = await self.async_client.embeddings.create(model=deployment, input=texts)
        return [d.embedding for d in resp.data]

    # ----------------------------
    # Images
    # ----------------------------
    async def generate_image_async(
        self, prompt: str, n: int = 1, size: str = "1024x1024", model: str | None = None
    ) -> list[dict[str, str]]:
        deployment = model or self.image_deployment
        if not deployment:
            raise ValueError("Image deployment name is required")
        resp = await self.async_client.images.generate(
            model=deployment, prompt=prompt, n=n, size=size, quality="low"
        )
        results = []
        for img in resp.data:
            if getattr(img, "b64_json", None):
                results.append({"b64_json": img.b64_json})
            elif getattr(img, "url", None):
                results.append({"url": img.url})
        return results

    # ----------------------------
    # Audio (Whisper)
    # ----------------------------
    async def transcribe_audio_async(self, audio_file_path: str, model: str | None = None) -> str:
        deployment = model or self.whispering_deployment
        if not deployment:
            raise ValueError("Whisper deployment name is required")

        # open file in a worker so we don't block the loop with disk IO
        def _read_bytes(path: str) -> bytes:
            with open(Path(path), "rb") as f:
                return f.read()

        content = await asyncio.to_thread(_read_bytes, audio_file_path)

        # The SDK accepts file-like objects; provide bytes via a NamedBytesIO if needed
        import io

        file_obj = io.BytesIO(content)
        file_obj.name = Path(audio_file_path).name  # some SDKs read .name

        resp = await self.async_client.audio.transcriptions.create(
            model=deployment,
            file=file_obj,
        )
        return resp.text
