"""REASONING-0.1A: additive structured-output support on the existing LLM clients."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.base.infrastructure.ai.azure_openai_client import AzureOpenAIClient
from src.base.infrastructure.ai.llm_usage import collect_llm_usage
from src.base.infrastructure.ai.openai_client import OpenAIClient
from src.base.infrastructure.ai.structured_output import (
    StructuredOutputIncomplete,
    StructuredOutputRefused,
    StructuredOutputUnsupported,
)
from src.base.repositories.openai_repository import OpenAIRepository

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["a"],
    "properties": {"a": {"type": "integer"}},
}


def response(**overrides: Any) -> SimpleNamespace:
    fields = {
        "status": "completed",
        "output": [],
        "output_text": '{"a": 1}',
        "model": "served-model",
        "usage": {"input_tokens": 11, "output_tokens": 7},
    }
    return SimpleNamespace(**{**fields, **overrides})


def clients() -> list[Any]:
    openai = OpenAIClient(api_key="test-key", chat_model="chat-model")
    azure = AzureOpenAIClient(
        endpoint="https://example.invalid/openai/v1",
        api_key="test-key",
        chat_deployment="chat-deployment",
        completion_deployment="chat-deployment",
        whispering_deployment="whisper",
    )
    return [openai, azure]


def fake(client: Any, result: Any) -> AsyncMock:
    create = AsyncMock(return_value=result)
    client.async_client = SimpleNamespace(responses=SimpleNamespace(create=create))
    return create


async def call(target: Any, **overrides: Any) -> str:
    kwargs = {
        "schema_name": "probe_output",
        "json_schema": SCHEMA,
        "max_output_tokens": 600,
        **overrides,
    }
    return await target.get_structured_response_async(
        [{"role": "user", "content": "data"}], "instructions", **kwargs
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("client", clients(), ids=["openai", "azure"])
async def test_structured_call_is_strict_bounded_tool_free_and_unstored(client) -> None:
    assert client.supports_structured_output is True
    create = fake(client, response())
    usage: list[dict[str, Any]] = []
    with collect_llm_usage(usage):
        assert await call(client) == '{"a": 1}'
    kwargs = create.await_args.kwargs
    assert kwargs["text"] == {
        "format": {"type": "json_schema", "name": "probe_output", "schema": SCHEMA, "strict": True}
    }
    assert kwargs["max_output_tokens"] == 600 and kwargs["store"] is False
    assert kwargs["model"] == client.chat_deployment
    assert "tools" not in kwargs and "tool_choice" not in kwargs and "temperature" not in kwargs
    assert usage == [
        {
            "provider": "openai",
            "model": "served-model",
            "tokens": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
            "latency_ms": usage[0]["latency_ms"],
        }
    ]
    await call(client, model="explicit-model")
    assert create.await_args.kwargs["model"] == "explicit-model"


@pytest.mark.asyncio
@pytest.mark.parametrize("client", clients(), ids=["openai", "azure"])
async def test_incomplete_refused_and_textless_responses_are_typed_errors(client) -> None:
    fake(client, response(status="incomplete"))
    with pytest.raises(StructuredOutputIncomplete):
        await call(client)
    refusal = SimpleNamespace(content=[SimpleNamespace(type="refusal", refusal="no")])
    fake(client, response(output=[refusal]))
    with pytest.raises(StructuredOutputRefused):
        await call(client)
    fake(client, response(output_text=None))
    with pytest.raises(Exception, match="no text"):
        await call(client)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"max_output_tokens": 0},
        {"max_output_tokens": 100_000},
        {"max_output_tokens": True},
        {"schema_name": "bad name!"},
        {"json_schema": {"type": "array"}},
    ],
)
async def test_invalid_arguments_never_reach_the_provider(overrides) -> None:
    client = clients()[0]
    create = fake(client, response())
    with pytest.raises(ValueError):
        await call(client, **overrides)
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_free_text_method_is_unchanged() -> None:
    client = clients()[0]
    create = fake(client, response(output_text="hello"))
    tools = [{"type": "function", "name": "x"}]
    assert (
        await client.get_response_async("hi", "be brief", tools=tools, temperature=0.2) == "hello"
    )
    kwargs = create.await_args.kwargs
    assert kwargs["tools"] == tools and "text" not in kwargs and "max_output_tokens" not in kwargs


@pytest.mark.asyncio
async def test_repository_requires_an_explicit_capability_and_logs_no_bodies(caplog) -> None:
    plain = SimpleNamespace(get_structured_response_async=AsyncMock())  # no capability flag
    repo = OpenAIRepository(plain)  # type: ignore[arg-type]
    assert repo.supports_structured_output is False
    with pytest.raises(StructuredOutputUnsupported):
        await call(repo)
    plain.get_structured_response_async.assert_not_awaited()

    client = clients()[0]
    client.async_client = SimpleNamespace(
        responses=SimpleNamespace(
            create=AsyncMock(side_effect=RuntimeError("SECRET prompt body sk-test"))
        )
    )
    repo = OpenAIRepository(client)  # type: ignore[arg-type]
    assert repo.supports_structured_output is True
    with caplog.at_level(logging.DEBUG), pytest.raises(RuntimeError):
        await call(repo)
    assert "SECRET" not in caplog.text and "sk-test" not in caplog.text
    assert "RuntimeError" in caplog.text
