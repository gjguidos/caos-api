# src/domains/agentverse/llm_calls/transcribe_mixin.py
from __future__ import annotations

import inspect
from typing import BinaryIO, Union

from src.domains.agentverse.agents.mixins.utils.LLMCallsMixin.helpers.helpers import (
    filter_kwargs,
    get_attr_chain,
)
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)

AudioLike = Union[bytes, bytearray, BinaryIO, str]  # noqa: UP007


class TranscriptionLLMMixin:
    def llm_transcribe(self, *, audio: bytes | BinaryIO | str, **opts) -> dict | str:
        """
        Speech-to-text (Whisper-style). Tries common method names/shapes on the repository.
        `audio` can be raw bytes, a file-like object, or a filepath/URL (repo-dependent).
        """
        self._require("audio_in")

        llm = getattr(self, "llm", None)
        if llm is None:
            raise AttributeError("No llm backend bound on agent.llm")

        direct_candidates = [
            "transcribe",
            "transcription",
            "speech_to_text",
            "audio_to_text",
            "asr",
            "whisper",
            "transcriptions",
        ]

        fn = None
        name_used = None

        for nm in direct_candidates:
            if hasattr(llm, nm):
                cand = getattr(llm, nm)
                if callable(cand):
                    fn = cand
                    name_used = nm
                    break
                if hasattr(cand, "create") and callable(getattr(cand, "create")):
                    fn = getattr(cand, "create")
                    name_used = f"{nm}.create"
                    break

        if fn is None:
            for dotted in (
                "transcriptions.create",
                "audio.transcriptions.create",
                "audio.speech_to_text",
            ):
                got, used = get_attr_chain(llm, dotted)
                if got:
                    fn = got
                    name_used = used
                    break

        if fn is None:
            raise AttributeError(
                f"{llm.__class__.__name__} exposes no speech-to-text method (tried: "
                f"{', '.join(direct_candidates + ['transcriptions.create', 'audio.transcriptions.create', 'audio.speech_to_text'])})"
            )

        call_kwargs = {}
        sig = inspect.signature(fn)

        common = {
            "audio": audio,
            "file": audio,
            "input": audio,
        }
        for k, v in common.items():
            if k in sig.parameters:
                call_kwargs[k] = v
                break

        call_kwargs.update(filter_kwargs(fn, **opts))

        audio_desc = (
            f"bytes({len(audio)}B)"
            if isinstance(audio, (bytes, bytearray))
            else f"file-like({getattr(audio, 'name', 'stream')})"
            if hasattr(audio, "read")
            else str(audio)
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.COGNITION_BAY,
            "[🧠 AUDIO][🎙️ TRANSCRIBE] transcribe_start",
            extra={
                "backend_type": type(llm).__name__,
                "method": name_used,
                "audio": audio_desc,
                "option_keys": [k for k, v in opts.items() if v is not None],
            },
        )

        result = fn(**call_kwargs)

        AGENTVERSE_LOGGER.info(
            LogDomain.COGNITION_BAY,
            "[🧠 AUDIO][✅ TRANSCRIBE] transcribe_complete",
            extra={
                "backend_type": type(llm).__name__,
                "method": name_used,
                "result_type": type(result).__name__ if result is not None else None,
            },
        )

        return result
