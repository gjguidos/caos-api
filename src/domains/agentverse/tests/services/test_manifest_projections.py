"""RG-02F projections authorize public descriptions, never operative manifests."""
import copy
import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.base.entities.user import User
from src.domains.agentverse.exceptions import WorkflowNotFoundError
from src.domains.agentverse.registries.base import Registry
from src.domains.agentverse.services.registry_service import RegistryService
from src.domains.agentverse.services.workflow_service import WorkflowService

USER = User(id="owner", tenant_id="tenant")


def catalog():
    return RegistryService({kind: Registry(kind) for kind in RegistryService.CLIENT_KINDS})


def put(svc, kind, entry_key, **metadata):
    svc._registries[kind].register(name=entry_key, key=entry_key, component=object, metadata={"ownership": "global", **metadata})


def assert_denied(svc, kind, key, user=USER):
    assert key not in svc.list_for_client(kind, user)
    with pytest.raises(KeyError):
        svc.get_info_for_client(kind, key, user)


@pytest.fixture
def production():
    # Import actual declarations/registrations; never instantiate providers or
    # run startup/Genesis, seed the DB, activate an agent or call external APIs.
    for module in (
        "tools.date_time_tool", "tools.analyze_tool", "tools.refine_tool", "tools.evaluate_tool",
        "tools.writer_tool", "tools.ideation_tool", "tools.green_claim_tool.green_claim_tool",
        "tools.web_crawler_tool", "tools.workflow_tool", "tools.draw_tool",
        "workflows.crawl_operation_v1.crawl_operation_v1",
        "workflows.mini_workflow.mini_workflow", "workflows.nested_mini_workflow.nested_mini_workflow_v1",
    ):
        importlib.import_module("src.domains.agentverse." + module)
    from src.domains.agentverse.registries.registries import registries
    svc = catalog()
    for kind, registry in registries.items():
        if kind in svc._registries:
            for item in registry.items():
                put(svc, kind, item.key, **copy.deepcopy(item.metadata))
    from src.domains.agentverse.agents.specs.chat_agent import chat_spec
    put(svc, "agent", "chat", **chat_spec.model_dump(mode="json"))
    for role in ("builtin", "writer_assistant", "compliance_auditor", "crawler"):
        spec = importlib.import_module("src.domains.agentverse.agents.roles." + role + ".spec").role_spec
        put(svc, "role", spec.key, **spec.to_metadata())
    return svc


def test_real_chat_and_three_valid_roles_visible_without_operative_manifests(production):
    svc = production
    raw = copy.deepcopy(svc.get_info("agent", "chat").metadata)
    agent = svc.get_info_for_client("agent", "chat", USER).metadata
    assert "chat" in svc.list_for_client("agent", USER)
    assert agent["default_tools"] == []
    assert not {"prompt", "resources", "memory", "tools", "safety", "telemetry", "spawn", "budget", "eval_probes"} & agent.keys()
    assert agent["default_config"]["max_history"] == 16
    assert agent["config_schema"]["properties"]["style"]["enum"] == ["friendly", "neutral", "formal"]
    for key in ("general", "writer_assistant", "compliance_auditor"):
        item = svc.get_info_for_client("role", key, USER)
        assert key in svc.list_for_client("role", USER)
        assert "system_tools" not in item.metadata["capabilities"]
        assert "handlers_present" not in item.metadata and "policy" not in item.metadata
    for key in ("datetime", "analyze", "refine", "evaluate"):
        assert_denied(svc, "tool", key)
    assert svc.get_info("agent", "chat").metadata == raw


def test_real_crawler_ui_removes_hidden_controls_across_all_projections(production):
    svc = production
    raw = copy.deepcopy(svc.get_info("tool", "web_crawler").metadata)
    detail = svc.get_info_for_client("tool", "web_crawler", USER).metadata
    listed = next(row for row in svc.list_for_client("tool", USER, include_metadata=True) if row["key"] == "web_crawler")
    assert {k: v for k, v in listed.items() if k not in ("key", "name", "description", "version")} == {k: v for k, v in detail.items() if k not in ("key", "name", "description", "version")}
    assert "auth_mode" not in json.dumps(detail)
    assert "browser_session_id" not in json.dumps(detail)
    assert not {"config", "default_config", "runtime_dependencies", "provider", "synaptic", "instructions"} & detail.keys()
    source = {c["id"]: c for c in raw["ui"]["components"]}
    components = {c["id"]: c for c in detail["ui"]["components"]}
    assert "url" in components and "output_profile" in components and "start_crawl" in components
    for key, component in components.items():
        assert component == source[key]
    assert detail["ui"]["config"]["chat_input"] == raw["ui"]["config"]["chat_input"]
    assert detail["ui"]["config"]["output_profiles"]["default"] == "media_light"
    assert svc.get_info("tool", "web_crawler").metadata == raw


def test_real_missing_crawl_dependency_remains_blocked_and_no_registrations_invented(production):
    svc = production
    assert svc._registries["tool"].get_info("artifact_builder") is None
    assert_denied(svc, "workflow", "crawl_operation_v1")
    assert_denied(svc, "role", "crawler")
    # A test-only trusted registration proves valid internal composition works;
    # this is deliberately not added to the production registry.
    put(svc, "tool", "artifact_builder", ownership="system", exposure="internal", config={"credential": "SENTINEL"})
    assert "crawl_operation_v1" in svc.list_for_client("workflow", USER)
    role = svc.get_info_for_client("role", "crawler", USER).metadata
    assert role["capabilities"]["form_source"] == {"type": "tool", "key": "web_crawler"}
    assert role["capabilities"]["optional_tools"] == {}
    assert role["capabilities"]["submit_target"]["key"] == "crawl_operation_v1"
    flow = svc.get_info_for_client("workflow", "crawl_operation_v1", USER).metadata
    assert "definition" not in flow and "tools" not in flow
    assert "SENTINEL" not in json.dumps([role, flow])
    assert_denied(svc, "tool", "artifact_builder")


@pytest.mark.parametrize("policy", [
    {}, {"ownership": None}, {"ownership": "tenant", "tenant_id": "other"},
    {"ownership": "global", "tenant_id": "tenant"},
    {"ownership": "tenant", "tenant_id": "tenant", "visibility": "private", "creator": "other"},
])
def test_internal_dependency_scope_cannot_be_borrowed_or_forged(policy):
    svc = catalog()
    svc._registries["tool"].register(name="dep", component=object, metadata=policy)
    put(svc, "role", "parent", capabilities={"system_tools": {"phase": "dep"}})
    assert_denied(svc, "role", "parent")


@pytest.mark.parametrize("policy", [{"ownership": "system"}, {"ownership": "global", "exposure": "internal"}, {"ownership": "tenant", "tenant_id": "tenant", "exposure": "internal"}])
def test_registered_internal_composition_is_valid_but_not_client_readable(policy):
    svc = catalog()
    put(svc, "tool", "dep", **policy)
    put(svc, "role", "parent", default_tools=["dep"], capabilities={"system_tools": {"phase": "dep"}})
    assert svc.get_info_for_client("role", "parent", USER).metadata["default_tools"] == []
    assert_denied(svc, "tool", "dep")
    if policy.get("ownership") == "tenant":
        assert_denied(svc, "role", "parent", User(id="owner", tenant_id="other"))


@pytest.mark.parametrize("body", [
    {"tools": ["missing"]},
    {"capabilities": {"required_tools": {"missing": "label"}}},
    {"unknown_extension": {"nested": [{"tool_key": "missing"}]}},
    {"unknown_extension": [[{"workflow_key": "missing"}]]},
    {"definition": {"steps": [{"type": "tool", "command": "@workflow --workflow_key=missing"}]}},
    {"tools": "missing"},
])
def test_missing_required_references_including_omitted_fields_fail_closed(body):
    svc = catalog()
    put(svc, "tool", "workflow")
    put(svc, "role", "parent", **body)
    assert_denied(svc, "role", "parent")


def test_internal_nested_graph_is_validated_and_cycles_are_bounded():
    svc = catalog()
    put(svc, "tool", "a", ownership="system", tools=["b"])
    put(svc, "tool", "b", ownership="system", tools=["a"], extra={"tool_key": "missing"})
    put(svc, "role", "parent", default_tools=["a"])
    assert_denied(svc, "role", "parent")
    put(svc, "tool", "b", ownership="system", tools=["a"])
    assert "parent" in svc.list_for_client("role", USER)


@pytest.mark.parametrize("kind", sorted(RegistryService.CLIENT_KINDS))
def test_unknown_fields_and_inclusion_flags_never_become_client_fields(kind):
    svc = catalog()
    put(svc, kind, "parent", include_internal=True, include_hidden=True, client_projection={"secret": "SENTINEL"}, config={"password": "SENTINEL"}, extra={"secret": "SENTINEL"}, synaptic={"trusted_lines": ["SENTINEL"]}, parameters_schema={"x-secret": "SENTINEL", "$defs": {"secret": "SENTINEL"}})
    item = svc.get_info_for_client(kind, "parent", USER)
    assert "SENTINEL" not in json.dumps(item.model_dump(mode="json"))
    item.metadata["mutated"] = True
    assert "mutated" not in svc.get_info(kind, "parent").metadata
    with pytest.raises(TypeError):
        svc.list_for_client(kind, USER, include_internal=True)


@pytest.mark.asyncio
async def test_persisted_workflow_details_use_strict_refs_and_typed_editor_projection(monkeypatch):
    from src.domains.agentverse.registries.registries import registries
    svc = catalog()
    put(svc, "tool", "open")
    put(svc, "tool", "internal", ownership="system")
    monkeypatch.setitem(registries, "tool", svc._registries["tool"])
    raw = {"key": "flow", "name": "Flow", "ownership": "tenant", "tenant_id": "tenant", "definition": {"steps": [{"id": "a", "type": "tool", "command": "@open", "config": {"password": "SENTINEL"}}], "handlers": {"secret": "SENTINEL"}}, "inputs": [{"key": "x", "label": "X", "secret": "SENTINEL"}], "ui": {"config": {"secret": "SENTINEL"}}}
    db = SimpleNamespace(find_one=AsyncMock(return_value=raw), find_all=AsyncMock(return_value=[raw]))
    service = WorkflowService(db)
    projected = await service.get_workflow("flow", USER)
    assert "SENTINEL" not in json.dumps(projected.model_dump())
    assert projected.definition["steps"][0]["command"] == "@open"
    assert len(await service.list_workflows(USER)) == 1
    assert raw["definition"]["handlers"]["secret"] == "SENTINEL"
    raw["definition"]["steps"][0]["command"] = "@internal"
    with pytest.raises(WorkflowNotFoundError):
        await service.get_workflow("flow", USER)
    assert await service.list_workflows(USER) == []


def test_soul_drops_security_metadata_and_subclass_extensions(monkeypatch):
    from src.domains.agentverse.entities.agent_soul_protocol_parts.agent_soul_protocol import AgentSoulProtocol
    from src.domains.agentverse.registries.registries import registries
    from src.domains.agentverse.services.personality_service import PersonalityService
    svc = catalog()
    put(svc, "personality", "public")
    monkeypatch.setitem(registries, "personality", svc._registries["personality"])
    service = PersonalityService()
    original = AgentSoulProtocol(basic_profile={"name": "Public"}, agent_soul_profile_metadata={"unlock_phrase": "SENTINEL", "security_protocol_log": ["SENTINEL"]}, extension="SENTINEL")
    service.extract_soul = Mock(return_value=original)
    projected = service.extract_soul_for_client("public", USER)
    assert projected.minimal_dump()["basic_profile"]["name"] == "Public"
    assert "SENTINEL" not in json.dumps(projected.model_dump(mode="json"))
    assert "agent_soul_profile_metadata" not in projected.minimal_dump()
    assert original.agent_soul_profile_metadata.unlock_phrase == "SENTINEL"


@pytest.mark.parametrize("policy", [{"ownership": "tenant", "tenant_id": "other"}, {"ownership": None}, {"exposure": "internal"}, {"hidden": True}])
def test_inline_component_scope_is_checked_before_dto_discards_policy(policy):
    svc = catalog()
    component = {"id": "private", "widget": "TextField", "props": {"label": "SENTINEL"}, **policy}
    put(svc, "tool", "parent", ui={"components": [component]})
    assert "SENTINEL" not in json.dumps(svc.get_info_for_client("tool", "parent", USER).metadata)


def test_missing_visible_ui_reference_is_invalid_not_silently_truncated():
    svc = catalog()
    put(svc, "tool", "parent", ui={"components": [{"id": "run", "widget": "ActionButton", "props": {"action": {"type": "tool_execute", "tool_key": "missing"}}}]})
    assert_denied(svc, "tool", "parent")


def test_real_crawler_navigation_and_cancel_contracts_survive_projection(production):
    """Keep the pre-existing frontend navigation/cancellation ABI, not raw internals."""
    detail = production.get_info_for_client("tool", "web_crawler", USER).metadata
    raw = production.get_info("tool", "web_crawler").metadata
    ui = detail["ui"]
    visible = {component["id"] for component in ui["components"]}
    config = ui["config"]
    assert [p["id"] for p in config["feature_panels"]] == [
        p["id"] for p in raw["ui"]["config"]["feature_panels"]
        if any(field in visible for field in p.get("fields", []))
        or any(any(field in visible for field in group.get("fields", [])) for group in p.get("groups", []))
    ]
    for panel in config["feature_panels"]:
        assert set(panel.get("fields", [])) <= visible
        for group in panel.get("groups", []):
            assert set(group["fields"]) <= visible
    search = config["search_behavior"]
    assert search["field"] == "find"
    for inference in search["inference"]:
        assert inference["source_options"] in visible
        assert inference["target_field"] in visible
    cancel = config["operation_binding"]["actions"]["cancel"]
    assert cancel == {
        "type": "http_request",
        "method": "POST",
        "endpoint": "/v1/operations/{run_id}/cancel",
        "path_params": {"run_id": {"from_state": "operation.run_id"}},
    }


def test_projection_filters_hidden_panel_references_and_unreviewed_cancel_endpoints():
    from src.domains.agentverse.security.manifest_projection import project_metadata

    raw = {
        "ownership": "global",
        "ui": {
            "components": [
                {"id": "find", "widget": "TextField"},
                {"id": "extensions", "widget": "SelectField"},
                {"id": "secret", "widget": "HiddenField"},
            ],
            "config": {
                "feature_panels": [
                    {"id": "search", "fields": ["find", "secret"],
                     "groups": [{"id": "hidden", "fields": ["secret"]}]},
                    {"id": "secret-panel", "fields": ["secret"]},
                ],
                "search_behavior": {"field": "find", "inference": [
                    {"match": "option_value", "source_options": "extensions", "target_field": "secret"},
                    {"match": "option_value", "source_options": "extensions", "target_field": "find"},
                ]},
                "operation_binding": {"actions": {"cancel": {
                    "type": "http_request", "method": "POST", "endpoint": "https://example.org/exfiltrate",
                    "path_params": {"run_id": {"from_state": "operation.run_id"}},
                }}},
            },
        },
    }
    result = project_metadata("tool", raw, lambda *_: True, lambda *_: True, lambda *_: True)
    config = result["ui"]["config"]
    assert [p["id"] for p in config["feature_panels"]] == ["search"]
    assert config["feature_panels"][0]["fields"] == ["find"]
    assert config["feature_panels"][0]["groups"] == []
    assert len(config["search_behavior"]["inference"]) == 1
    assert config["search_behavior"]["inference"][0]["target_field"] == "find"
    assert not config["operation_binding"].get("actions")
