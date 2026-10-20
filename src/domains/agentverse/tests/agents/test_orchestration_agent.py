from src.domains.agentverse.agents.orchestration_agent.orchestration_agent import (  # noqa: F401
    OrchestrationAgent,
)
from src.domains.agentverse.entities.agent import Agent
from src.domains.agentverse.entities.agent_request import AgentRequest
from src.domains.agentverse.registries.registries import agent_registry_instance
from src.domains.agentverse.tools.spec import AgentToolRef


def _orchestration_request(**overrides) -> AgentRequest:
    defaults = dict(
        user_id="user-1",
        name="My Orchestrator",
        type="orchestration",
        personality_key="empty_eva",
        prompt="",
        tools=[AgentToolRef(key="workflow", config={})],
        workflows=["mini_workflow_v1"],
    )
    defaults.update(overrides)
    return AgentRequest(**defaults)


class TestOrchestrationAgentRegistry:
    def test_registry_resolves_orchestration_type(self) -> None:
        cls = agent_registry_instance.get("orchestration")
        assert cls is OrchestrationAgent

    def test_orchestration_agent_is_subclass_of_chat_agent(self) -> None:
        from src.domains.agentverse.agents.chat_agent.chat_agent import ChatAgent

        assert issubclass(OrchestrationAgent, ChatAgent)


class TestOrchestrationToolRequirements:
    def test_workflow_tool_is_not_required_for_now(self) -> None:
        req = _orchestration_request(tools=[])

        assert req.type == "orchestration"
        assert req.tools == []

    def test_attached_workflows_are_not_required_for_now(self) -> None:
        req = _orchestration_request(workflows=[])

        assert req.tools[0].key == "workflow"
        assert req.workflows == []

    def test_valid_request_does_not_raise(self) -> None:
        req = _orchestration_request()
        assert req.workflows == ["mini_workflow_v1"]

    def test_other_tools_alongside_workflow_are_allowed(self) -> None:
        req = _orchestration_request(
            tools=[
                AgentToolRef(key="datetime", config={}),
                AgentToolRef(key="workflow", config={}),
            ]
        )
        assert [tool.key for tool in req.tools] == ["datetime", "workflow"]


class TestAgentDtoContract:
    def test_agent_dto_exposes_type(self) -> None:
        agent = Agent(
            id="a1",
            name="Test Orch",
            system_name="test_orch",
            type="orchestration",
            personality_key="empty_eva",
            prompt="",
            chat_url="/chat/a1",
            llm_type="openai",
            audio_type="elevenlabs",
            db_type=None,
            cache_type="redis",
            knowledge_db_type=None,
            messaging_type=None,
            dna_sequence="abc123",
            tools=[AgentToolRef(key="workflow", config={})],
            workflows=["mini_workflow_v1"],
        )
        d = agent.model_dump()
        assert d["type"] == "orchestration"

    def test_agent_dto_exposes_tool_config(self) -> None:
        agent = Agent(
            id="a1",
            name="Test Orch",
            system_name="test_orch",
            type="orchestration",
            personality_key="empty_eva",
            prompt="",
            chat_url="/chat/a1",
            llm_type="openai",
            audio_type="elevenlabs",
            db_type=None,
            cache_type="redis",
            knowledge_db_type=None,
            messaging_type=None,
            dna_sequence="abc123",
            tools=[AgentToolRef(key="workflow", config={"max_steps_per_call": 25})],
            workflows=["mini_workflow_v1"],
        )
        d = agent.model_dump()
        tools = d["tools"]
        assert len(tools) == 1
        assert tools[0]["key"] == "workflow"
        assert tools[0]["config"]["max_steps_per_call"] == 25
        assert d["workflows"] == ["mini_workflow_v1"]
