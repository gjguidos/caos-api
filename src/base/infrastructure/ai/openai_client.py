
from __future__ import annotations

import asyncio
import io
import logging
import time
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from src.base.infrastructure.ai.llm_usage import record_llm_call
from src.base.infrastructure.ai.structured_output import create_structured_response
from src.base.utils.message_logger import MessageLogger

logger = logging.getLogger(__name__)


class OpenAIClient:
    """
    Async OpenAI client for CAOS.

    Supports:
    - Responses API
    - Embeddings
    - Image generation
    - Audio transcription

    Realtime audio will use a separate session client.
    """

    provider: str = "openai"
    # Explicit capability: Responses API strict json_schema output (REASONING-0.1A).
    supports_structured_output: bool = True

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        chat_model: str = "gpt-6-astra",
        completion_model: str | None = None,
        embedding_model: str | None = None,
        whispering_model: str | None = None,
        image_model: str | None = None,
        request_timeout: float = 180.0,
    ):
        if not api_key or not api_key.strip():
            raise ValueError("Missing OpenAI API key")

        self.async_client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=request_timeout,
        )

        self.endpoint = base_url

        # Preserve the existing client interface
        self.chat_deployment = chat_model
        self.completion_deployment = (
            completion_model or chat_model
        )
        self.embedding_deployment = embedding_model
        self.whispering_deployment = whispering_model
        self.image_deployment = image_model

        self._write_request_and_response_to_file = False

        if self._write_request_and_response_to_file:
            self.message_logger = MessageLogger()

        configured_models = {
            "chat": chat_model,
            "embedding": embedding_model,
            "image": image_model,
            "audio": whispering_model,
        }

        active_models = {
            capability: model
            for capability, model in configured_models.items()
            if model is not None
        }

        logger.info(
            "OpenAIClient initialized — models=%s",
            active_models,
        )

    # ----------------------------
    # Responses API
    # ----------------------------

    async def get_response_async(
        self,
        input: str | list[dict[str, Any]],
        instructions: str | None = None,
        tools: list[dict] | None = None,
        temperature: float | None = 0.7,
        model: str | None = None,
    ) -> str:

        selected_model = model or self.chat_deployment

        logger.debug(
            "[OpenAI/Responses] Invoking model: %s",
            selected_model,
        )

        request: dict[str, Any] = {
            "model": selected_model,
            "input": input,
            "store": False,
        }

        if instructions is not None:
            request["instructions"] = instructions

        if tools is not None:
            request["tools"] = tools

        # Do not send temperature by default to newer
        # reasoning models that may not support it.
        # Model-specific parameter capabilities can be
        # introduced later.
        if (
            temperature is not None
            and not selected_model.startswith("gpt-6")
        ):
            request["temperature"] = temperature

        started = time.perf_counter()

        response = await self.async_client.responses.create(
            **request
        )

        record_llm_call(
            provider=self.provider,
            model=(
                getattr(response, "model", None)
                or selected_model
            ),
            usage=getattr(response, "usage", None),
            latency_ms=int(
                (time.perf_counter() - started) * 1000
            ),
        )

        if self._write_request_and_response_to_file:
            self.message_logger.write_messages_to_file(
                input, selected_model
            )
            self.message_logger.write_messages_to_text_file(
                input, selected_model
            )
            self.message_logger.log_response(
                response.output_text, selected_model
            )

        return response.output_text


    # ----------------------------
    # Structured output (REASONING-0.1A, additive)
    # ----------------------------

    async def get_structured_response_async(
        self,
        input: str | list[dict[str, Any]],
        instructions: str,
        *,
        schema_name: str,
        json_schema: dict[str, Any],
        max_output_tokens: int,
        model: str | None = None,
    ) -> str:
        """Schema-constrained call; no tools, no storage, no prompt/output logging."""
        return await create_structured_response(
            self.async_client,
            provider=self.provider,
            model=model or self.chat_deployment,
            input=input,
            instructions=instructions,
            schema_name=schema_name,
            json_schema=json_schema,
            max_output_tokens=max_output_tokens,
        )

    # ----------------------------
    # Embeddings
    # ----------------------------

    async def get_embeddings_async(
        self,
        texts: list[str],
        model: str | None = None,
    ) -> list[list[float]]:

        selected_model = model or self.embedding_deployment

        if not selected_model:
            raise ValueError(
                "OpenAI embedding model is required"
            )

        response = await self.async_client.embeddings.create(
            model=selected_model,
            input=texts,
        )

        return [
            item.embedding
            for item in response.data
        ]

    # ----------------------------
    # Image Generation
    # ----------------------------

    async def generate_image_async(
        self,
        prompt: str,
        n: int = 1,
        size: str = "1024x1024",
        model: str | None = None,
    ) -> list[dict[str, str]]:

        logger.info(
            "OpenAI image request: model=%s size=%s quality=%s",
            model or self.image_deployment,
            size,
            n,
        )

        selected_model = model or self.image_deployment

        if not selected_model:
            raise ValueError(
                "OpenAI image model is required"
            )

        response = await self.async_client.images.generate(
            model=selected_model,
            prompt=prompt,
            n=n,
            size=size,
        )

        results: list[dict[str, str]] = []

        for image in response.data:
            if image.b64_json:
                results.append({
                    "b64_json": image.b64_json
                })
            elif image.url:
                results.append({
                    "url": image.url
                })

        return results

    # ----------------------------
    # Audio Transcription
    # ----------------------------

    async def transcribe_audio_async(
        self,
        audio_file_path: str,
        model: str | None = None,
    ) -> str:

        selected_model = (
            model or self.whispering_deployment
        )

        if not selected_model:
            raise ValueError(
                "OpenAI transcription model is required"
            )

        def read_file(path: str) -> bytes:
            return Path(path).read_bytes()

        content = await asyncio.to_thread(
            read_file, audio_file_path
        )

        file_obj = io.BytesIO(content)
        file_obj.name = Path(audio_file_path).name

        response = (
            await self.async_client.audio.transcriptions.create(
                model=selected_model,
                file=file_obj,
            )
        )

        return response.text

    async def close(self) -> None:
        await self.async_client.close()
