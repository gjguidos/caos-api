from unittest.mock import AsyncMock

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
    tool = ChatToolV2(config=ChatToolV2Config(history_window=1))

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
    tool = ChatToolV2(config=ChatToolV2Config(history_window=0))

    result = await tool.provide_context(
        text="Hello",
        history=[{"role": "user", "content": "Old"}],
    )

    assert result["messages"] == []


@pytest.mark.asyncio
async def test_standard_tool_execution_envelope():
    tool = ChatToolV2()

    result = await tool.execute(socket_emitter=None, text="Hello")

    assert result.success is True
    assert "messages" in result.result
    assert "hints" in result.result


@pytest.mark.asyncio
async def test_public_execution_preserves_context_and_does_not_emit():
    emitter = AsyncMock()
    tool = ChatToolV2(config=ChatToolV2Config(history_window=1))
    history = [
        {"role": "user", "content": "Old"},
        {"role": "user", "content": "Question"},
        {"role": "assistant", "content": "Answer"},
    ]

    result = await tool.execute(socket_emitter=emitter, text="Next", history=history)

    assert result.success is True
    assert result.result == await tool.provide_context(text="Next", history=history)
    assert result.result["messages"] == history[-2:]
    assert emitter.mock_calls == []


@pytest.mark.asyncio
async def test_disabled_public_execution_does_not_provide_context(monkeypatch):
    tool = ChatToolV2(config=ChatToolV2Config(enabled=False))
    provide_context = AsyncMock()
    monkeypatch.setattr(tool, "provide_context", provide_context)

    result = await tool.execute(socket_emitter=None, text="Hello")

    assert result["success"] is False
    assert result["code"] == "TOOL_DISABLED"
    provide_context.assert_not_awaited()


@pytest.mark.parametrize(
    ("tenant", "agent", "granted", "allowed"),
    [
        ("tenant.one", "agent.one", True, True),
        ("tenant.one", "agent.one", False, False),
        ("tenant.other", "agent.one", True, False),
        ("tenant.one", "agent.other", True, False),
    ],
)
@pytest.mark.asyncio
async def test_chat_context_binding_still_requires_scoped_cpbx_grant(
    monkeypatch, tenant, agent, granted, allowed
):
    from src.base.events.v2.contracts import EventPermissionError
    from src.base.events.v2.policy import EventPrincipal
    from src.domains.agentverse.ncsc.authorization import SynapticAuthorization
    from src.domains.agentverse.ncsc.cpbx import SynapticExchange
    from src.domains.agentverse.ncsc.models import SynapticGrants
    from src.domains.agentverse.registries.synaptic_registry import SynapticRegistry
    from src.domains.agentverse.tools.chat_tool_v2.synaptic import chat_context

    registry = SynapticRegistry()
    registry.subscribe("chat.context", provider="tool.chat")(chat_context)
    provider = AsyncMock(return_value={"messages": []})
    monkeypatch.setattr(ChatToolV2, "provide_context", provider)
    client = SynapticExchange(
        registry=registry,
        authorization=SynapticAuthorization(
            grants={
                ("tenant.one", "agent.one"): SynapticGrants(
                    invoke={"chat.context"} if granted else set()
                )
            }
        ),
    ).bind(EventPrincipal(agent, tenant))

    if allowed:
        assert await client.request("chat.context", {"text": "Hello"}) == {"messages": []}
        provider.assert_awaited_once_with(text="Hello", history=None)
    else:
        with pytest.raises(EventPermissionError):
            await client.request("chat.context", {"text": "Hello"})
        provider.assert_not_awaited()
