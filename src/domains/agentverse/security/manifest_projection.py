"""RG-02F client DTOs. Operative manifests are never serialized by this module.

Extra fields are discarded at each typed boundary. Schemas describe public form
fields, not an escape hatch for provider metadata, object defaults or references.
Adding a client field requires an explicit contract here; inclusion flags cannot
change this policy. Authorization and dependency validity belong to the service.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.domains.agentverse.tools.config_contract import config_contract

Scalar = str | int | float | bool | None


class ClientDTO(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


class Display(ClientDTO):
    ownership: Literal["global", "tenant", "system"] | None = None
    tenant_id: str | None = None
    exposure: Literal["public", "internal"] | None = None
    key: str | None = None
    name: str | None = None
    version: str | None = None
    title: str | None = None
    label: str | None = None
    summary: str | None = None
    description: str | None = None
    emoji: str | None = None
    icon: str | None = None
    color: str | None = None
    tags: list[str] = Field(default_factory=list)
    badges: list[str] = Field(default_factory=list)


class Option(ClientDTO):
    value: Scalar = None
    label: str | None = None
    description: str | None = None
    disabled: bool | None = None


class Condition(ClientDTO):
    field: str | None = None
    state: str | None = None
    operator: str | None = None
    value: Scalar | list[Scalar] = None
    message: str | None = None


class Flag(ClientDTO):
    flag: str = ""
    required: bool = False


class Action(ClientDTO):
    type: Literal["tool_execute", "operation_cancel"]
    tool_key: str | None = None
    operation_binding: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    # input and run_id contain state bindings, never literal credentials/config.
    input: dict[Literal["source", "include"], str] = Field(default_factory=dict)
    run_id: dict[Literal["from_state"], str] = Field(default_factory=dict)


class Props(ClientDTO):
    label: str | None = None
    description: str | None = None
    placeholder: str | None = None
    helperText: str | None = None
    defaultValue: Scalar | list[Scalar] = None
    options: list[Option] = Field(default_factory=list)
    min: float | int | None = None
    max: float | int | None = None
    step: float | int | None = None
    minLength: int | None = None
    maxLength: int | None = None
    multiline: bool | None = None
    rows: int | None = None
    required: bool | None = None
    disabled: bool | None = None
    multiple: bool | None = None
    accept: str | None = None
    variant: str | None = None
    action: Action | None = None


class ProfileBinding(ClientDTO):
    source: str | None = None
    apply_values: bool = True
    respect_user_overrides: bool = True


class Component(ClientDTO):
    id: str
    type: str | None = None
    widget: str
    group: str | None = None
    groupLabel: str | None = None
    flag_config: Flag = Field(default_factory=Flag)
    render_paths: list[str] = Field(default_factory=list)
    slot_order: int = 0
    props: Props = Field(default_factory=Props)
    file_upload: bool | None = None
    visible_when: Condition | None = None
    disabled_when: list[Condition] = Field(default_factory=list)
    profile_binding: ProfileBinding | None = None


class ChatInput(ClientDTO):
    id: str | None = None
    placeholder: str | None = None
    file_upload: bool = False
    flag: str | None = None
    required: bool = False


class Profile(ClientDTO):
    defaults: dict[str, Scalar | list[Scalar]] = Field(default_factory=dict)
    preview: dict[str, Scalar | list[Scalar]] = Field(default_factory=dict)


class OutputProfiles(ClientDTO):
    default: str | None = None
    controlled_fields: list[str] = Field(default_factory=list)
    profiles: dict[str, Profile] = Field(default_factory=dict)
    options: list[Option] = Field(default_factory=list)
    apply_values: bool = True
    respect_user_overrides: bool = True


class OperationBinding(ClientDTO):
    source: str | None = None
    kind: str | None = None
    run_id_state: str | None = None
    status_state: str | None = None
    running_statuses: list[str] = Field(default_factory=list)
    stopping_statuses: list[str] = Field(default_factory=list)
    terminal_statuses: list[str] = Field(default_factory=list)
    events: dict[Literal["started", "progress", "completed", "failed", "cancelled"], str] = Field(default_factory=dict)
    actions: OperationActions | None = None


class CancelPathValue(ClientDTO):
    from_state: str | None = None


class CancelHttpAction(ClientDTO):
    type: Literal["http_request"]
    method: Literal["POST"]
    endpoint: Literal["/v1/operations/{run_id}/cancel"]
    path_params: dict[Literal["run_id"], CancelPathValue] = Field(default_factory=dict)


class OperationActions(ClientDTO):
    cancel: CancelHttpAction | None = None


class FeatureGroup(ClientDTO):
    id: str
    label: str | None = None
    fields: list[str] = Field(default_factory=list)


class FeaturePanel(ClientDTO):
    id: str
    label: str | None = None
    description: str | None = None
    produces_report: str | None = None
    fields: list[str] = Field(default_factory=list)
    groups: list[FeatureGroup] = Field(default_factory=list)


class SearchInference(ClientDTO):
    match: Literal["option_value"]
    source_options: str
    target_field: str
    target_mode: Literal["append"] | None = None
    also_include_report: str | None = None


class SearchBehavior(ClientDTO):
    field: str
    produces_report: str | None = None
    inference: list[SearchInference] = Field(default_factory=list)


class UIConfig(ClientDTO):
    chat_input: ChatInput | None = None
    group_label_map: dict[str, str] = Field(default_factory=dict)
    output_profiles: OutputProfiles | None = None
    operation_binding: OperationBinding | None = None
    feature_panels: list[FeaturePanel] = Field(default_factory=list)
    search_behavior: SearchBehavior | None = None


class UI(ClientDTO):
    admin: AdminUI | None = None
    icon: str | None = None
    category: str | None = None
    display_hints: list[str] = Field(default_factory=list)
    config: UIConfig = Field(default_factory=UIConfig)
    components: list[Component] = Field(default_factory=list)


class Output(ClientDTO):
    key: str
    label: str
    type: Literal["string", "number", "boolean", "list", "object", "image"]
    path: str


class FileSlot(ClientDTO):
    name: str
    label: str
    accepted_mimes: list[str] | None = None
    required: bool = False


class Files(ClientDTO):
    max_count: int = 1
    max_size_bytes: int | None = None
    accepted_mimes: list[str] | None = None
    slots: list[FileSlot] = Field(default_factory=list)


class Supports(ClientDTO):
    streaming: bool | None = None
    attachments: list[str] = Field(default_factory=list)
    multi_turn_memory: bool | None = None
    files: Files | None = None


class ToolProjection(Display):
    required_rulepacks: bool = False
    min_rulepacks: int | None = None
    capabilities: list[str] = Field(default_factory=list)
    tool_class: str | None = None
    outputs: list[Output] = Field(default_factory=list)
    supports: Supports = Field(default_factory=Supports)
    requires_collection: bool = False


class AgentProjection(Display):
    department: str | None = None
    agent_group: str | None = None
    agent_subclass: str | None = None
    capabilities: list[str] = Field(default_factory=list)


class ResourceRef(ClientDTO):
    type: Literal["tool", "workflow"]
    key: str


class Interface(ClientDTO):
    type: str | None = None
    layout: str | None = None
    role_adapter: str | None = None


class Operation(ClientDTO):
    label: str | None = None
    description: str | None = None
    form_source: ResourceRef | None = None
    runner: ResourceRef | None = None
    result_contract: str | None = None


class Operations(ClientDTO):
    default: str | None = None
    items: dict[str, Operation] = Field(default_factory=dict)


class RoleCapabilities(ClientDTO):
    routing: str | None = None
    chat_fallback: bool | None = None
    refine_loop: str | None = None
    interface: Interface | None = None
    operations: Operations | None = None
    form_source: ResourceRef | None = None
    submit_target: ResourceRef | None = None
    required_tools: dict[str, str] = Field(default_factory=dict)
    optional_tools: dict[str, str] = Field(default_factory=dict)
    required_workflows: dict[str, str] = Field(default_factory=dict)


class RoleProjection(Display):
    agent_group: str | None = None
    agent_subclass: str | None = None
    docs: dict[str, str] = Field(default_factory=dict)
    links: dict[str, str] = Field(default_factory=dict)


class Rule(ClientDTO):
    id: str
    description: str
    severity: str
    applies_to: str = "all"
    domain: str | None = None
    pack_id: str | None = None
    tags: list[str] = Field(default_factory=list)


class RulepackProjection(Display):
    domain: str | None = None
    rules: list[Rule] = Field(default_factory=list)
    deprecated: bool = False


class Input(ClientDTO):
    key: str
    label: str
    input: str = "text"
    required: bool = False
    help: str | None = None
    default: Scalar | list[Scalar] = None
    options: list[Option] | None = None
    constraints: InputConstraints | None = None


class Pause(ClientDTO):
    prompt: str | None = None


class Retry(ClientDTO):
    attempts: int = 1
    backoff: Literal["fixed", "exponential"] = "fixed"
    delay_seconds: int | float = 0


class AuthoringStep(ClientDTO):
    id: str
    type: Literal["tool", "pause", "end"] = "tool"
    label: str | None = None
    command: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    file_bindings: dict[str, str] = Field(default_factory=dict)
    pause: Pause | None = None
    retry: Retry | None = None


class AuthoringDefinition(ClientDTO):
    execution_mode: Literal["sequential", "dag"] = "sequential"
    steps: list[AuthoringStep] = Field(default_factory=list)


class InputConstraints(ClientDTO):
    accepted_mimes: list[str] | None = None
    max_count: int | None = None
    max_size_bytes: int | None = None
    min: int | float | None = None
    max: int | float | None = None


class AdminUI(ClientDTO):
    fields: dict[str, Input] = Field(default_factory=dict)


class WorkflowProjection(Display):
    step_count: int | None = None
    has_pause: bool | None = None
    inputs: list[Input] = Field(default_factory=list)


PROJECTIONS: dict[str, type[Display]] = {
    "agent": AgentProjection, "role": RoleProjection, "tool": ToolProjection,
    "workflow": WorkflowProjection, "personality": Display, "rulepack": RulepackProjection,
}
# Configuration is runtime data by default. These are the reviewed public form
# knobs for agent/role declarations, independent of whatever a schema advertises.
PUBLIC_CONFIG = frozenset({
    "max_history", "style", "enable_tools", "temperature", "quality_threshold",
    "allow_intermediate_render", "offer_next_step", "cta_style", "cta_persona",
    "cta_max_words", "locale", "layout", "submit_mode", "tool_resolution",
    "result_renderer", "runtime_events", "role_adapter", "default_operation",
})
REFERENCE_FIELDS = frozenset({
    "tool_key", "workflow_key", "personality_key", "role_name", "tools", "default_tools",
    "required_tools", "system_tools", "required_workflows", "optional_tools", "rule_pack_ids",
    "rule_packs", "uses_workflows", "workflows", "key", "provider_key", "trusted_lines",
})


def dump(model: ClientDTO) -> dict[str, Any]:
    return model.model_dump(mode="json", exclude_unset=True)


def public_schema(raw: Any, *, fields: frozenset[str] | None = None, depth: int = 0) -> dict[str, Any]:
    """Construct a bounded JSON Schema vocabulary; never copy schema extensions.

    Object-valued defaults/examples/$defs/$ref are intentionally not client data.
    Reference-address properties require their own explicit DTO, not schema values.
    """
    if not isinstance(raw, dict) or depth > 16 or raw.get("writeOnly") or raw.get("hidden"):
        return {}
    if any(k in raw for k in ("ownership", "tenant_id", "tenant_scoped")):
        return {}
    if raw.get("format") == "password" or raw.get("exposure") == "internal":
        return {}
    out: dict[str, Any] = {}
    for key in ("type", "title", "description", "format", "minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems", "pattern"):
        value = raw.get(key)
        if isinstance(value, (str, int, float, bool)) or (key == "type" and isinstance(value, list) and all(isinstance(v, str) for v in value)):
            out[key] = value
    if isinstance(raw.get("properties"), dict):
        props = {key: public_schema(value, depth=depth + 1) for key, value in raw["properties"].items() if key not in REFERENCE_FIELDS and not key.startswith("_") and (fields is None or key in fields)}
        out["properties"] = {key: value for key, value in props.items() if value}
        out["required"] = [key for key in raw.get("required", []) if key in out["properties"]]
        out["additionalProperties"] = False
    elif isinstance(raw.get("items"), dict):
        out["items"] = public_schema(raw["items"], depth=depth + 1)
    # Only primitive value schemas carry literal values.
    if raw.get("type") in ("string", "number", "integer", "boolean", "null"):
        for key in ("default", "const", "enum"):
            value = raw.get(key)
            if key in raw and (value is None or isinstance(value, (str, int, float, bool)) or (isinstance(value, list) and all(v is None or isinstance(v, (str, int, float, bool)) for v in value))):
                out[key] = value
    return out


def project_metadata(kind: str, metadata: dict[str, Any], readable: Callable[[str, str], bool], inline_allowed: Callable[[dict[str, Any]], bool], dependencies_valid: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    out = dump(PROJECTIONS[kind].model_validate(metadata))
    if kind == "tool":
        raw_ui = metadata.get("ui") or {}
        # Independent UI policy cannot be erased by parsing a DTO. Arbitrary
        # extensions are omitted, while policies on returned nodes are enforced.
        if any(k in raw_ui for k in ("ownership", "tenant_id", "tenant_scoped", "exposure", "visibility", "hidden")) and not inline_allowed({k: v for k, v in raw_ui.items() if k != "components"}):
            raw_ui = {}
        visible = [c for c in raw_ui.get("components", []) if isinstance(c, dict) and not c.get("hidden") and c.get("widget") != "HiddenField" and c.get("exposure") != "internal" and c.get("visibility") != "hidden"]
        # Generic HTTP actions can contain arbitrary credentials, response maps
        # and hidden-control bindings. They are outside the reviewed form ABI.
        visible = [c for c in visible if (c.get("props", {}).get("action") or {}).get("type") in (None, "tool_execute", "operation_cancel")]
        if any(not dependencies_valid(c) for c in visible):
            raise ValueError("Unresolved public UI dependency")
        visible = [c for c in visible if not (target := (c.get("props", {}).get("action") or {}).get("tool_key")) or readable("tool", target)]
        if any(not dependencies_valid(c) for c in visible):
            raise ValueError("Unresolved public UI dependency")
        visible = [c for c in visible if inline_allowed(c)]
        public_fields = frozenset(c["id"] for c in visible)
        config = dict(raw_ui.get("config") or {})
        if not inline_allowed(config):
            config = {}
        profiles = config.get("output_profiles")
        if isinstance(profiles, dict):
            profiles = dict(profiles)
            profiles["controlled_fields"] = [k for k in profiles.get("controlled_fields", []) if k in public_fields]
            profiles["profiles"] = {
                name: {section: {k: v for k, v in body.get(section, {}).items() if k in public_fields and (v is None or isinstance(v, (str, int, float, bool)) or (isinstance(v, list) and all(isinstance(x, (str, int, float, bool)) for x in v)))} for section in ("defaults", "preview")}
                for name, body in profiles.get("profiles", {}).items() if isinstance(body, dict)
            }
            config["output_profiles"] = profiles
        # Feature panels and search inference are purely a client navigation
        # projection. Never serialize references to filtered/hidden controls.
        feature_panels = []
        for panel in config.get("feature_panels", []):
            if not isinstance(panel, dict) or not inline_allowed(panel):
                continue
            groups = []
            for group in panel.get("groups", []):
                if not isinstance(group, dict) or not inline_allowed(group):
                    continue
                fields = [f for f in group.get("fields", []) if isinstance(f, str) and f in public_fields]
                if fields:
                    groups.append({**group, "fields": fields})
            fields = [f for f in panel.get("fields", []) if isinstance(f, str) and f in public_fields]
            if groups or fields:
                feature_panels.append({**panel, "fields": fields, "groups": groups})
        config["feature_panels"] = feature_panels
        search = config.get("search_behavior")
        if isinstance(search, dict) and search.get("field") in public_fields and inline_allowed(search):
            inference = []
            for entry in search.get("inference", []):
                if (isinstance(entry, dict)
                    and entry.get("source_options") in public_fields
                    and entry.get("target_field") in public_fields
                    and inline_allowed(entry)):
                    inference.append(entry)
            config["search_behavior"] = {**search, "inference": inference}
        else:
            config.pop("search_behavior", None)

        # Cancellation's fixed endpoint is an explicitly reviewed client ABI;
        # reject arbitrary HTTP endpoints or methods rather than forwarding them.
        binding = config.get("operation_binding")
        if isinstance(binding, dict):
            binding = dict(binding)
            cancel = ((binding.get("actions") or {}).get("cancel") or {})
            valid_cancel = (isinstance(cancel, dict)
                and cancel.get("type") == "http_request"
                and cancel.get("method") == "POST"
                and cancel.get("endpoint") == "/v1/operations/{run_id}/cancel"
                and cancel.get("path_params") == {"run_id": {"from_state": "operation.run_id"}})
            binding["actions"] = {"cancel": cancel} if valid_cancel else {}
            config["operation_binding"] = binding
        # Agent-specific configuration uses the Tool's own admin declarations,
        # not its invocation controls. Never expose raw provider config or class paths.
        admin = raw_ui.get("admin") or {}
        admin_fields = {}
        raw_config_schema = metadata.get("config_schema")
        config_model = config_contract(raw_config_schema) if admin else None
        if config_model is not None:
            raw_config_schema = config_model.model_json_schema()
        safe_config_properties = public_schema(raw_config_schema).get("properties")
        if isinstance(admin, dict) and inline_allowed(
            {k: v for k, v in admin.items() if k != "fields"}
        ):
            for key, field in (admin.get("fields") or {}).items():
                if (
                    isinstance(field, dict)
                    and key not in REFERENCE_FIELDS
                    and not key.startswith("_")
                    and not field.get("hidden")
                    and not field.get("writeOnly")
                    and field.get("input") != "password"
                    and (safe_config_properties is None or key in safe_config_properties)
                    and inline_allowed(field)
                    and dependencies_valid(field)
                ):
                    admin_fields[key] = dump(
                        Input.model_validate(
                            {**field, "key": key, "label": field.get("label", key)}
                        )
                    )
        out["ui"] = dump(
            UI.model_validate(
                {
                    **raw_ui,
                    "admin": {"fields": admin_fields},
                    "config": config,
                    "components": visible,
                }
            )
        )
        chat = out["ui"].get("config", {}).get("chat_input") or {}
        if chat.get("flag"):
            public_fields |= {chat["flag"].removeprefix("--")}
        # UI defaults belong to visible controls; raw provider config never does.
        for key in ("parameters_schema", "config_schema"):
            if isinstance(metadata.get(key), dict):
                out[key] = public_schema(metadata[key], fields=public_fields)
        # Only explicitly presented Agent configuration knobs may reveal their
        # Tool-owned schema. Never serialize Python paths, provider defaults or
        # invocation schemas as a substitute for Agent configuration.
        if admin_fields:
            out["agent_config_schema"] = public_schema(
                raw_config_schema, fields=frozenset(admin_fields)
            )
    elif kind in ("role", "agent"):
        if kind == "role":
            caps = dict(metadata.get("capabilities") or {})
            for field in ("required_tools", "optional_tools"):
                caps[field] = {k: v for k, v in caps.get(field, {}).items() if readable("tool", k)}
            caps["required_workflows"] = {k: v for k, v in caps.get("required_workflows", {}).items() if readable("workflow", v)}
            for field in ("form_source", "submit_target"):
                ref = caps.get(field)
                if isinstance(ref, dict) and not readable(ref.get("type", ""), ref.get("key", "")):
                    caps.pop(field, None)
            operations = caps.get("operations")
            if isinstance(operations, dict):
                caps["operations"] = {**operations, "items": {k: v for k, v in operations.get("items", {}).items() if all(not isinstance(v.get(field), dict) or readable(v[field].get("type", ""), v[field].get("key", "")) for field in ("runner", "form_source"))}}
            out["capabilities"] = dump(RoleCapabilities.model_validate(caps))
        out["config_schema"] = public_schema(metadata.get("config_schema"), fields=PUBLIC_CONFIG)
        props = out["config_schema"].get("properties", {})
        out["default_config"] = {k: v for k, v in (metadata.get("default_config") or {}).items() if k in props and (v is None or isinstance(v, (str, int, float, bool)))}
        # The frontend's defaults are references, never serialized AgentSpecs.
        refs = metadata.get("default_tools", metadata.get("tools", []))
        out["default_tools"] = [r if isinstance(r, str) else {"key": r["key"], "name": r.get("name", r["key"])} for r in refs if isinstance(r, (str, dict)) and isinstance((key := r if isinstance(r, str) else r.get("key")), str) and readable("tool", key)]
    return out


Input.model_rebuild()
UI.model_rebuild()


def project_authoring_workflow(raw: dict[str, Any]) -> dict[str, Any]:
    """Narrow existing persisted editor ABI, after strict full-manifest UTU.

    Public authoring commands are client-authored text, not provider config.
    Unsupported operative set/wait_event steps fail closed instead of returning
    a truncated graph an editor could accidentally save over the original.
    Registry templates use summaries and never expose their operative graph.
    """
    from src.domains.agentverse.entities.workflow import WorkflowDocument

    doc = WorkflowDocument.model_validate(raw).model_dump(mode="json")
    doc["definition"] = dump(AuthoringDefinition.model_validate(raw["definition"]))
    doc["inputs"] = [dump(Input.model_validate(i)) for i in raw.get("inputs", [])]
    doc["ui"] = dump(UI.model_validate(raw.get("ui") or {}))
    return doc
