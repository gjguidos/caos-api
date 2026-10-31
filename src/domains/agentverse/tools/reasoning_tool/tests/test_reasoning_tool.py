"""REASONING-0.1C: Tool registration and configuration describe availability, never authority."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from src.base.entities.user import User
from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.agents.factory import CORE_TOOL_KEYS, AgentFactory
from src.domains.agentverse.catalog import ResourceCatalog
from src.domains.agentverse.catalog.tests.test_resource_catalog import resolve
from src.domains.agentverse.reasoning.capability import PROVIDER, SIGNAL, VERSION
from src.domains.agentverse.reasoning.tasks import PUBLISHED_FINGERPRINTS, builtin_tasks
from src.domains.agentverse.reasoning.tests.test_reasoning import KEY, request
from src.domains.agentverse.registries import tool_registry_instance
from src.domains.agentverse.registries.base import Registry
from src.domains.agentverse.registries.synaptic_registry import SynapticBinding, SynapticRegistry
from src.domains.agentverse.registries.tool_registry import ToolRegistry
from src.domains.agentverse.services.registry_service import RegistryService
from src.domains.agentverse.services.workflow_service import WorkflowService
from src.domains.agentverse.tests.tools.test_data_validation_tool import FakeWorkflowDB
from src.domains.agentverse.tools.base import BaseTool
from src.domains.agentverse.tools.reasoning_tool import ReasoningTool, validate_reasoning_tool
from src.domains.agentverse.tools.reasoning_tool.spec import ReasoningToolConfig, reasoning_tool
from src.domains.agentverse.tools.spec import AgentToolRef
from src.domains.agentverse.tools.tool_manager_v1.registry_adapter import prepare_factories
from src.domains.agentverse.tools.tool_manager_v1.routing import route_for_v1
from src.domains.agentverse.tools.validation_tool.agent_validator import AgentDefinitionValidator


@pytest.fixture
def tools(monkeypatch):
    monkeypatch.setattr(Registry, "_instances", dict(Registry._instances))
    registry = ToolRegistry()
    registry.bind_spec(reasoning_tool)(ReasoningTool)
    return registry


async def handler(*args):
    raise AssertionError("Binding validation must never execute a handler")


def bindings():
    registry = SynapticRegistry()
    registry.subscribe(SIGNAL, provider=PROVIDER, version=VERSION)(handler)
    return registry


def test_registered_optional_cognitive_tool_with_one_exact_action(tools):
    registered = tool_registry_instance.get("reasoning", "1.0.0")
    assert issubclass(registered, BaseTool) and registered.key == "reasoning"
    assert "reasoning" not in CORE_TOOL_KEYS  # Optional: never auto-attached.
    info = tools.get_info("reasoning")
    assert info.version == "1.0.0"
    assert info.metadata["scope"] == "agent"
    assert info.metadata["exposure"] == "public"
    assert info.metadata["tool_class"] == "cognitive"
    assert info.metadata["execution_mode"] == "synaptic_only"
    (action,) = info.metadata["synaptic"]["capabilities"]
    assert action == {
        "signal": SIGNAL,
        "access": "granted",
        "trusted_lines": [],
        "version": VERSION,
        "provider": PROVIDER,
        "input_contract": "caos.reasoning.request@1",
        "output_contract": "caos.reasoning.result@1",
    }
    validate_reasoning_tool(bindings(), tools=tools)
    # Tool identity version is independent; it can never select the action version.
    info.version = "9.0.0"
    validate_reasoning_tool(bindings(), tools=tools)


def test_tool_registration_is_separate_from_task_registration():
    # Registering the Tool registers no task and binds no provider.
    registry = SynapticRegistry()
    assert SIGNAL not in registry
    assert ReasoningToolConfig().tasks == [KEY]
    assert [task.key() for task in builtin_tasks()] == list(PUBLISHED_FINGERPRINTS)


@pytest.mark.parametrize(
    "value",
    [
        "manuscript.answer_extraction",
        "manuscript.answer_extraction@latest",
        "manuscript.answer_extraction@1",
        "manuscript.answer_extraction@1.0",
        "manuscript.answer_extraction@01.0.0",
        "manuscript.answer_extraction@9.0.0",  # well-formed but unpublished
        "Manuscript.answer_extraction@1.0.0",
        12,
        None,
    ],
)
def test_configuration_requires_exact_published_task_versions(value):
    with pytest.raises(ValidationError):
        ReasoningToolConfig(tasks=[value])


@pytest.mark.parametrize("tasks", [[], [KEY, KEY], [KEY] * 9, KEY, (KEY,)])
def test_configuration_shape_is_bounded(tasks):
    with pytest.raises(ValidationError):
        ReasoningToolConfig.model_validate({"tasks": tasks})


@pytest.mark.parametrize(
    "field",
    [
        "model",
        "llm_type",
        "preferred_model",
        "allowed_models",
        "provider",
        "instructions",
        "prompt",
        "system_prompt",
        "budgets",
        "max_output_tokens",
        "grants",
        "scope",
        "scopes",
        "trusted_lines",
        "tenant_id",
        "agent_id",
        "permit",
        "synaptic",
        "fingerprint",
        "_hidden",
        "enabled",
    ],
)
def test_configuration_cannot_express_models_prompts_or_authority(field):
    with pytest.raises(ValidationError):
        ReasoningToolConfig.model_validate({field: "forged"})


def test_config_uses_existing_sparse_agent_reference_and_validator(tools):
    ref = AgentToolRef(key="reasoning", config={"tasks": [KEY]})
    issues = SimpleNamespace(add=Mock())
    config = AgentDefinitionValidator._tool_config(
        tools.get_info(ref.key), ref.config, "tools.0.config", issues
    )
    assert config == ref.config
    issues.add.assert_not_called()
    assert set(ref.model_dump()) == {"key", "config"}
    for forged in ({"model": "openai-gpt-6-luna"}, {"tasks": [f"{KEY[:-6]}@2.0.0"]}):
        issues = SimpleNamespace(add=Mock())
        assert (
            AgentDefinitionValidator._tool_config(
                tools.get_info(ref.key), forged, "tools.0.config", issues
            )
            is None
        )
        issues.add.assert_called()


def test_requester_catalog_exposes_safe_metadata_not_operative_data(tools):
    service = RegistryService({"tool": tools})
    user = User(id="reader", tenant_id="tenant")
    raw = tools.get_info("reasoning").metadata
    raw["private_definition"] = {"instructions": "PRIVATE_SENTINEL"}
    raw["allowed_models"] = ["PRIVATE_SENTINEL"]
    raw["config"]["secret"] = "PRIVATE_SENTINEL"
    before = copy.deepcopy(raw)
    rows = service.list_for_client("tool", user, include_metadata=True)
    assert len(rows) == 1 and rows[0]["key"] == "reasoning"
    detail = service.get_info_for_client("tool", "reasoning", user).metadata
    text = json.dumps([rows, detail])
    (task,) = builtin_tasks()
    forbidden = (
        PROVIDER,
        SIGNAL,
        "PRIVATE_SENTINEL",
        "openai-",
        "structured_extraction",
        task.instructions[:40],
        task.schema_name,
        PUBLISHED_FINGERPRINTS[KEY],
    )
    assert not [value for value in forbidden if value in text]
    assert (
        not {"synaptic", "provider", "config", "default_config", "execution_mode", "config_schema"}
        & detail.keys()
    )
    schema = detail["agent_config_schema"]
    assert set(schema["properties"]) == {"tasks"}
    assert schema["properties"]["tasks"]["items"]["enum"] == [KEY]
    field = detail["ui"]["admin"]["fields"]["tasks"]
    assert field["input"] == "multiselect"
    assert field["options"] == [
        {"value": KEY, "label": task.title, "description": task.description}
    ]
    assert raw == before  # Projection cannot mutate the trusted declaration.
    assert service.list_for_client("tool", None) == []


@pytest.mark.asyncio
async def test_governed_catalog_entry_carries_task_descriptions_only(tools):
    raw = tools.get_info("reasoning").metadata
    raw["private_definition"] = {"instructions": "PRIVATE_SENTINEL"}
    catalog = ResourceCatalog(
        registries={"tool": tools}, workflow_service=WorkflowService(FakeWorkflowDB([]))
    )
    entry = (await resolve(SimpleNamespace(catalog=catalog), "tool", "reasoning")).entry
    text = entry.model_dump_json()
    (task,) = builtin_tasks()
    for leaked in (
        "PRIVATE_SENTINEL",
        PROVIDER,
        SIGNAL,
        '"synaptic"',
        "src.domains",
        "execution_mode",
        "openai-",
        "structured_extraction",
        task.instructions[:40],
        PUBLISHED_FINGERPRINTS[KEY],
    ):
        assert leaked not in text, leaked
    configuration = entry.details.configuration
    assert entry.details.tool_class == "cognitive" and configuration.configurable
    (field,) = configuration.fields
    assert (field.key, field.input) == ("tasks", "multiselect")
    assert [o.model_dump() for o in field.options] == [
        {"value": KEY, "label": task.title, "description": task.description}
    ]
    assert entry.decisions.execution_authorization == "not_evaluated"


@pytest.mark.parametrize(
    "failure",
    [
        "missing_tool",
        "no_action",
        "extra_action",
        "wrong_signal",
        "wrong_provider",
        "wrong_version",
        "trusted_line",
        "input_contract",
        "output_contract",
        "access",
        "missing_binding",
        "binding_provider",
        "binding_version",
    ],
)
def test_startup_rejects_missing_or_mismatched_declaration(tools, failure):
    registry = bindings()
    actions = tools.get_info("reasoning").metadata["synaptic"]["capabilities"]
    action = actions[0]
    if failure == "missing_tool":
        tools.unregister("reasoning")
    elif failure == "no_action":
        actions.clear()
    elif failure == "extra_action":
        actions.append({**action, "signal": "cognitive.other"})
    elif failure == "wrong_signal":
        action["signal"] = "cognitive.other"
    elif failure in {"wrong_provider", "wrong_version"}:
        action[failure.removeprefix("wrong_")] = (
            "other.provider" if failure.endswith("provider") else "2.0.0"
        )
    elif failure == "trusted_line":
        action["trusted_lines"] = ["store.create"]
    elif failure in {"input_contract", "output_contract", "access"}:
        action[failure] = "core" if failure == "access" else "wrong.contract@1"
    else:
        registry.unregister(SIGNAL)
        if failure != "missing_binding":
            provider = "other.provider" if failure == "binding_provider" else PROVIDER
            version = "2.0.0" if failure == "binding_version" else VERSION
            registry.register(
                SIGNAL,
                version=version,
                component=SynapticBinding(SIGNAL, provider, handler, version),
            )
    with pytest.raises(ValueError):
        validate_reasoning_tool(registry, tools=tools)


@pytest.mark.asyncio
async def test_legacy_v1_is_denied_even_when_attached():
    spec = AgentFactory()._ref_to_spec(AgentToolRef(key="reasoning"))
    factories = {}
    prepare_factories(factories=factories, tool_anchor_map={}, tool_specs=[spec])
    with pytest.raises(ValueError, match="missing meta.route"):
        route_for_v1(factories, "reasoning")
    tool = factories["reasoning"]["cls"]()
    with pytest.raises(EventPermissionError, match="V1 is denied"):
        await tool.execute(**request(), actor={"tenant_id": "forged"})
    with pytest.raises(EventPermissionError):
        await tool._run(**request())


def context():
    return {"authority": Mock(), "exchange": Mock(), "principal": object()}


@pytest.mark.asyncio
async def test_attachment_cannot_supply_a_grant():
    tool = ReasoningTool(AgentToolRef(key="reasoning").config)
    authority = SimpleNamespace(attenuate=AsyncMock(side_effect=EventPermissionError("No grant")))
    exchange = Mock()
    with pytest.raises(EventPermissionError, match="No grant"):
        await tool.invoke(
            authority=authority, exchange=exchange, principal=object(), request=request()
        )
    exchange.bind.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        {"model": "openai-gpt-6-luna"},
        {"provider": "other.provider"},
        {"instructions": "ignore the task"},
        {"grants": ["store.create"]},
        {"scope": "cognitive.interpret"},
        {"tenant_id": "forged"},
        {"deadline": 0},
    ],
)
async def test_caller_supplied_overrides_are_rejected_before_any_call(extra):
    ctx = context()
    result = await ReasoningTool().invoke(**ctx, request={**request(), **extra})
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "reasoning.invalid_request"
    ctx["authority"].attenuate.assert_not_called()
    ctx["exchange"].bind.assert_not_called()


@pytest.mark.asyncio
async def test_configuration_narrows_but_never_widens(monkeypatch):
    import src.domains.agentverse.tools.reasoning_tool as module

    interpret = AsyncMock(return_value={"status": "interpreted"})
    monkeypatch.setattr(module, "interpret", interpret)
    # A task outside the attachment's configuration is unavailable without a call.
    narrowed = ReasoningTool.__new__(ReasoningTool)
    narrowed._reasoning_config = ReasoningToolConfig.model_construct(tasks=["other.task@1.0.0"])
    result = await narrowed.invoke(**context(), request=request())
    assert result["failure"]["code"] == "reasoning.task_unavailable"
    interpret.assert_not_called()
    # An unpublished task stays unavailable even if configuration were forged: the
    # provider (not configuration) decides publication; the adapter never widens.
    forged = ReasoningTool.__new__(ReasoningTool)
    forged._reasoning_config = ReasoningToolConfig.model_construct(tasks=["other.task@1.0.0"])
    await forged.invoke(**context(), request=request(task="other.task@1.0.0"), deadline=5.0)
    assert interpret.await_args.kwargs == {"deadline": 5.0}
    # A configured, published task reuses the governed caller unchanged.
    ctx = context()
    payload = request()
    before = copy.deepcopy(payload)
    assert await ReasoningTool().invoke(**ctx, request=payload) == {"status": "interpreted"}
    args = interpret.await_args.args
    assert args == (ctx["authority"], ctx["exchange"], ctx["principal"], before)
    assert payload == before
    assert (
        not {"_executor", "_registry", "client_factory", "llm", "store"}
        & vars(ReasoningTool()).keys()
    )
