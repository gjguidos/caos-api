from types import SimpleNamespace

from src.domains.agentverse.registries.registries import tool_registry_instance
from src.domains.agentverse.tools.base import BaseTool
from src.domains.agentverse.tools.spec import ToolSpec
from src.domains.agentverse.tools.tool_manager import ToolManager


class _AllowedWriterTool(BaseTool):
    name = "AgentVerse Test Writer Tool"
    key = "agentverse_test_writer"
    version = "1.0.0"
    parameters = {}


class _RemoteHelloWorldTool(BaseTool):
    name = "AgentVerse Test Hello World Tool"
    key = "agentverse_test_hello_world"
    version = "1.0.0"
    parameters = {}


def _agent() -> SimpleNamespace:
    return SimpleNamespace(
        id="agent-1",
        name="test-agent",
        type="test",
        metadata={},
        capabilities=set(),
        llm=None,
        image=None,
        video=None,
        audio=None,
        db=None,
        cache=None,
        vectordb=None,
        messaging=None,
        http={},
    )


def test_mcp_proxy_tools_are_not_loaded_unless_configured() -> None:
    tool_registry_instance.register_tool(
        key=_AllowedWriterTool.key,
        name=_AllowedWriterTool.name,
        component=_AllowedWriterTool,
    )
    tool_registry_instance.register_tool(
        key=_RemoteHelloWorldTool.key,
        name=_RemoteHelloWorldTool.name,
        component=_RemoteHelloWorldTool,
        metadata={"source": "mcp_proxy"},
    )

    try:
        manager = ToolManager(
            _agent(),
            [ToolSpec(key=_AllowedWriterTool.key, name=_AllowedWriterTool.name)],
        )

        assert manager.list_tool_keys() == [_AllowedWriterTool.key]
        assert manager.ensure_instance(_RemoteHelloWorldTool.key) is None
    finally:
        tool_registry_instance.unregister(_AllowedWriterTool.key)
        tool_registry_instance.unregister(_RemoteHelloWorldTool.key)


def test_configured_mcp_proxy_tool_can_still_be_used() -> None:
    tool_registry_instance.register_tool(
        key=_RemoteHelloWorldTool.key,
        name=_RemoteHelloWorldTool.name,
        component=_RemoteHelloWorldTool,
        metadata={"source": "mcp_proxy"},
    )

    try:
        manager = ToolManager(
            _agent(),
            [ToolSpec(key=_RemoteHelloWorldTool.key, name=_RemoteHelloWorldTool.name)],
        )

        assert manager.list_tool_keys() == [_RemoteHelloWorldTool.key]
        assert manager.ensure_instance(_RemoteHelloWorldTool.key) is not None
    finally:
        tool_registry_instance.unregister(_RemoteHelloWorldTool.key)
