"""Additive, schema-constrained Responses API calls (REASONING-0.1A).

Shared by the OpenAI and Azure OpenAI clients. Existing ``get_response_async`` behavior
is untouched. This path:

* sends a strict ``json_schema`` text format and an explicit ``max_output_tokens``;
* never sends tools (so no tool can be called), never stores the exchange;
* records usage through the existing ``record_llm_call`` side channel;
* raises typed, body-free errors and never logs prompts, inputs or outputs.

The returned text is only *well-formed by the provider's account*; callers must still
validate it locally against their own strict schema.
"""

from __future__ import annotations

import time
from typing import Any

from src.base.infrastructure.ai.llm_usage import record_llm_call

# Provider-side ceilings for a single structured call. Tasks choose lower values.
MAX_STRUCTURED_OUTPUT_TOKENS = 4096
MAX_SCHEMA_NAME = 64


class StructuredOutputError(RuntimeError):
    """Base for safe structured-output failures; messages never carry provider bodies."""

    code = "structured_output.failed"


class StructuredOutputUnsupported(StructuredOutputError):
    """The backend cannot honor schema-constrained output; never silently degraded."""

    code = "structured_output.unsupported"


class StructuredOutputIncomplete(StructuredOutputError):
    """The provider stopped early (e.g. the output-token limit); the text is unusable."""

    code = "structured_output.incomplete"


class StructuredOutputRefused(StructuredOutputError):
    """The model declined to answer."""

    code = "structured_output.refused"


def _validate_arguments(
    schema_name: str, json_schema: dict[str, Any], max_output_tokens: int
) -> None:
    if (
        not isinstance(schema_name, str)
        or not schema_name
        or len(schema_name) > MAX_SCHEMA_NAME
        or not schema_name.replace("_", "").isalnum()
    ):
        raise ValueError("Invalid structured-output schema name")
    if not isinstance(json_schema, dict) or json_schema.get("type") != "object":
        raise ValueError("Structured output requires an object JSON Schema")
    if (
        not isinstance(max_output_tokens, int)
        or isinstance(max_output_tokens, bool)
        or not 1 <= max_output_tokens <= MAX_STRUCTURED_OUTPUT_TOKENS
    ):
        raise ValueError("Invalid max_output_tokens")


def _refused(response: Any) -> bool:
    for item in getattr(response, "output", None) or ():
        for part in getattr(item, "content", None) or ():
            if getattr(part, "type", None) == "refusal":
                return True
    return False


async def create_structured_response(
    async_client: Any,
    *,
    provider: str,
    model: str,
    input: str | list[dict[str, Any]],
    instructions: str,
    schema_name: str,
    json_schema: dict[str, Any],
    max_output_tokens: int,
) -> str:
    """One schema-constrained call. Raises ``StructuredOutputError`` subclasses or the
    transport's own exceptions (callers map them to safe codes)."""
    _validate_arguments(schema_name, json_schema, max_output_tokens)
    started = time.perf_counter()
    response = await async_client.responses.create(
        model=model,
        input=input,
        instructions=instructions,
        max_output_tokens=max_output_tokens,
        text={
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "schema": json_schema,
                "strict": True,
            }
        },
        store=False,
    )
    record_llm_call(
        provider=provider,
        model=getattr(response, "model", None) or model,
        usage=getattr(response, "usage", None),
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
    if getattr(response, "status", None) == "incomplete":
        raise StructuredOutputIncomplete("Structured output was incomplete")
    if _refused(response):
        raise StructuredOutputRefused("The model declined the request")
    text = getattr(response, "output_text", None)
    if not isinstance(text, str):
        raise StructuredOutputError("Structured output carried no text")
    return text
