from __future__ import annotations

import base64
import logging
import tempfile
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from src.base.handlers.exception import classify_error
from src.base.repositories.openai_repository import OpenAIRepository
from src.domains.agentverse.tools.base import BaseTool, ToolResult

logger = logging.getLogger("agentverse.tools.whisperer_tool")


# ------------------------- Config -------------------------


class WhispererToolConfig(BaseModel):
    class Config:
        validate_by_name = True
        extra = "ignore"


# ------------------------- Tool -------------------------


class WhispererTool(BaseTool):
    """
    Transcribes short audio snippets (webm/wav) using your OpenAIRepository.
    """

    name = "Whisperer Tool"
    version = "1.0.0"
    tool_class = "whisperer"
    parameters = {"audio_base64": "Base64-encoded audio file (webm or wav format)"}

    def __init__(
        self,
        transcription_client: OpenAIRepository,
        config: WhispererToolConfig | dict | None = None,
    ):
        if not transcription_client:
            raise ValueError("[WhispererTool] Missing required dependency: transcription_client")
        self.transcription_client = transcription_client

        if isinstance(config, dict):
            # pydantic v1 compat
            try:
                config = WhispererToolConfig.parse_obj(config)  # type: ignore[attr-defined]
            except Exception:
                config = WhispererToolConfig(**config)
        self.config: WhispererToolConfig = config or WhispererToolConfig()

    async def execute(self, audio_base64: str) -> ToolResult:
        try:
            audio_bytes = base64.b64decode(audio_base64)
            with tempfile.NamedTemporaryFile(suffix=".webm") as temp_audio_file:
                temp_audio_file.write(audio_bytes)
                temp_audio_file.flush()

                transcription = await self.transcription_client.transcribe_audio_async(
                    temp_audio_file.name
                )
                return ToolResult(success=True, result=transcription)

        except Exception as e:
            logger.error("[WhispererTool] Transcription failed: %s", e, exc_info=True)
            message, _code = classify_error(e)
            return ToolResult(success=False, result=message, error=message)


# ------------------------- Late Registration -------------------------

_METADATA: dict[str, Any] = {
    "ownership": "global",
    "config_schema": "WhispererToolConfig",
    "config": {},
    "tags": ["builtin", "transcription", "audio", "whisper"],
    "key": "whisperer",
    "outputs": [
        {"key": "transcription", "label": "Transcription", "type": "string", "path": "result"},
    ],
}


def _apply_register_decorator(deco: Callable, cls: type) -> type:
    """
    Some registries return a decorator you apply to the class;
    others register immediately. Handle both shapes safely.
    """
    try:
        maybe = deco(  # type: ignore[misc]
            key=_METADATA.get("key", "whisperer"),
            name=WhispererTool.name,
            description="A tool that transcribes audio using OpenAI Whisper.",
            version=WhispererTool.version,
            metadata=_METADATA,
        )
        # If it's a decorator, apply it to the class and return the result.
        if callable(maybe):
            return maybe(cls)  # type: ignore[call-arg]
        # If it's immediate registration, just return the original class.
        return cls
    except TypeError:
        # Some registries have a different signature; try a more generic set
        maybe = deco(  # type: ignore[misc]
            name=WhispererTool.name,
            description="A tool that transcribes audio using OpenAI Whisper.",
            version=WhispererTool.version,
            metadata=_METADATA,
        )
        return maybe(cls) if callable(maybe) else cls


def register(registry: Any) -> type:
    """
    Explicit registration entry point. Call this from your bootstrap:
        from src.domains.agentverse.tools.whisperer_tool import register
        register(tool_registry_instance)
    Works with registries that expose either `register_tool` or `register`.
    """
    # Prefer new API if present
    if hasattr(registry, "register_tool") and callable(registry.register_tool):
        return _apply_register_decorator(registry.register_tool, WhispererTool)

    # Fallback to legacy API
    if hasattr(registry, "register") and callable(registry.register):
        maybe = registry.register(  # type: ignore[misc]
            name=WhispererTool.name,
            description="A tool that transcribes audio using OpenAI Whisper.",
            version=WhispererTool.version,
            metadata=_METADATA,
        )
        return maybe(WhispererTool) if callable(maybe) else WhispererTool

    logger.warning(
        "[WhispererTool] Registry does not expose register_tool/register; skipping registration."
    )
    return WhispererTool


# ------------------------- Public API for the loader -------------------------


def public_api() -> dict[str, Any]:
    """
    Keep auto-loaders away from raw module.__dict__ scanning.
    """
    return {
        "WhispererTool": WhispererTool,
        "WhispererToolConfig": WhispererToolConfig,
        "register": register,
        "metadata": _METADATA,
    }


__all__ = ["WhispererTool", "WhispererToolConfig", "register", "public_api"]
