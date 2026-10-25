
import pytest

from src.domains.agentverse.tools.chat_tool_v2 import (
    ChatToolV2,
    ChatToolV2Config,
)


@pytest.mark.asyncio
async def test_chat_v2_returns_context_without_llm():
    tool = ChatToolV2()

    result = await tool.provide_context(
        text="Hello",
        history=[
            {"role": "user", "content": "Previous question"},
            {"role": "assistant", "content": "Previous answer"},
        ],
    )

    assert len(result["messages"]) == 2
    assert result["hints"]["temperature"] == 0.3
    assert result["usage_meta"]["provider"] == "chat_v2"
    assert result["system_overrides"] == []
    assert result["evidence"] == []


@pytest.mark.asyncio
async def test_chat_v2_limits_history():
    tool = ChatToolV2(
        config=ChatToolV2Config(history_window=1)
    )

    history = [
        {"role": "user", "content": "First"},
        {"role": "assistant", "content": "First answer"},
        {"role": "user", "content": "Second"},
        {"role": "assistant", "content": "Second answer"},
    ]

    result = await tool.provide_context(
        text="Next question",
        history=history,
    )

    assert len(result["messages"]) == 2
    assert result["messages"][0]["content"] == "Second"


@pytest.mark.asyncio
async def test_zero_history_window():
    tool = ChatToolV2(
        config=ChatToolV2Config(history_window=0)
    )

    result = await tool.provide_context(
        text="Hello",
        history=[{"role": "user", "content": "Old"}],
    )

    assert result["messages"] == []


@pytest.mark.asyncio
async def test_standard_tool_execution_envelope():
    tool = ChatToolV2()

    result = await tool._run(text="Hello")

    assert result["success"] is True
    assert "messages" in result["result"]
    assert "hints" in result["result"]
