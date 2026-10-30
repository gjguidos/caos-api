"""Deterministic, read-only validation of an Agent Creation Input.

Reuses the existing requester-aware catalog policy (``RegistryService``) and workflow
read policy (``WorkflowService``). It never constructs agents or tools, never writes,
never calls an LLM and never decides authorization: a valid result grants nothing.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from src.base.entities.user import User
from src.base.operations.v2.admission import canonical_json, sha256_hex
from src.domains.agentverse.agents.conversational_composition_v2 import supports_v2_conversation
from src.domains.agentverse.entities.workflow import slugify_workflow_key
from src.domains.agentverse.exceptions import WorkflowNotFoundError
from src.domains.agentverse.registries.base import Registry, RegistryItem
from src.domains.agentverse.security import agent_access
from src.domains.agentverse.services.registry_service import RegistryService
from src.domains.agentverse.tools.config_contract import config_contract as _config_contract
from src.domains.agentverse.tools.tool_manager_v1.validation import BACKEND_ALIAS
from src.domains.agentverse.tools.validation_tool.models import (
    SERVER_CONTROLLED_FIELDS,
    AgentCreationInput,
    ValidationIssue,
    ValidationResult,
    validation_digest,
)
from src.domains.agentverse.utils.canonicalize import canonicalize

if TYPE_CHECKING:
    from src.domains.agentverse.services.workflow_service import WorkflowService

# Unknown and unreadable references share one code so a requester cannot probe
# another tenant's private catalog for existence.
UNAVAILABLE = "reference.unavailable"


class _Issues:
    def __init__(self) -> None:
        self._items: list[ValidationIssue] = []

    def add(self, path: str, code: str, message: str) -> None:
        self._items.append(ValidationIssue(path=path, code=code, message=message))

    def __bool__(self) -> bool:
        return bool(self._items)

    def sorted(self) -> list[ValidationIssue]:
        return sorted(self._items, key=lambda issue: (issue.path, issue.code, issue.message))


def _path(loc: tuple[Any, ...] | list[Any]) -> str:
    return ".".join(str(part) for part in loc)


def _pydantic_issues(issues: _Issues, exc: ValidationError, *, prefix: str = "") -> None:
    for error in exc.errors():
        path = _path((prefix, *error["loc"]) if prefix else error["loc"])
        if error["type"] == "missing":
            issues.add(path, "field.required", "Field is required")
        elif error["type"] == "extra_forbidden":
            field = error["loc"][-1] if error["loc"] else ""
            if not prefix and field in SERVER_CONTROLLED_FIELDS:
                issues.add(path, "field.server_controlled", "Field is managed by the server")
            else:
                issues.add(path, "field.unknown", "Field is not part of the contract")
        else:
            # ``msg`` describes the constraint; ``input`` is deliberately not echoed.
            issues.add(path, "field.invalid", error["msg"])


def _canonical(value: Any) -> Any:
    """JSON-safe, deterministic form of registry or catalog content for fingerprinting."""
    if isinstance(value, BaseModel):
        return _canonical(value.model_dump(mode="python"))
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_canonical(item) for item in value]
    if isinstance(value, set | frozenset):
        return sorted((_canonical(item) for item in value), key=canonical_json)
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, type) or callable(value):
        module = getattr(value, "__module__", "") or ""
        return f"{module.removeprefix('src.')}:{getattr(value, '__qualname__', '')}"
    # Unknown objects fingerprint by type and text; any instability only fails closed.
    return f"{type(value).__module__}:{type(value).__qualname__}:{value}"


def content_fingerprint(item: RegistryItem | Mapping[str, Any]) -> str:
    """Digest of a resolved dependency's content, excluding process-local registration time."""
    if isinstance(item, RegistryItem):
        content: Any = item.model_dump(
            mode="python", include={"key", "name", "description", "version", "metadata"}
        )
    else:
        content = item
    return sha256_hex(_canonical(content))


def _rulepack_minimum(metadata: Mapping[str, Any]) -> int:
    """Same semantics as ``AgentFactory._get_rulepack_requirement_for_tool``."""
    if not metadata.get("required_rulepacks"):
        return 0
    try:
        minimum = int(metadata.get("min_rulepacks") or 1)
    except (TypeError, ValueError):
        minimum = 1
    return max(1, minimum)


class AgentDefinitionValidator:
    """``data.validation`` for ``resource_type="agent"``."""

    resource_type = "agent"

    def __init__(
        self, *, registries: Mapping[str, Registry[Any]], workflow_service: WorkflowService
    ) -> None:
        self._registries = registries
        self._catalog = RegistryService(dict(registries))
        self._workflows = workflow_service

    async def validate(self, payload: dict[str, Any], *, requester: User) -> ValidationResult:
        issues = _Issues()
        try:
            data = AgentCreationInput.model_validate(payload)
        except ValidationError as exc:
            _pydantic_issues(issues, exc)
            return ValidationResult(valid=False, resource_type="agent", issues=issues.sorted())

        type_item = self._reference("agent", data.type, "type", requester, issues)
        role_item = self._reference("role", data.role_name, "role_name", requester, issues)
        personality_item = self._reference(
            "personality", data.personality_key, "personality_key", requester, issues
        )
        rule_packs = self._references(
            "rulepack", data.rule_pack_ids, "rule_pack_ids", requester, issues
        )
        tools, configs = self._tools(data, requester, issues)
        workflows = await self._resolve_workflows(data.workflows, requester, issues)
        self._whitelisted_groups(data.whitelisted_groups, requester, issues)

        workflow_tools = self._workflow_tools(workflows, issues)
        self._gui(data, type_item, role_item, workflows, issues)
        if not issues:
            self._composition(type_item, role_item, tools, workflow_tools, rule_packs, issues)
        if issues or type_item is None or role_item is None or personality_item is None:
            return ValidationResult(valid=False, resource_type="agent", issues=issues.sorted())

        normalized = {
            "name": data.name,
            "type": type_item.key,
            "role_name": role_item.key,
            "personality_key": personality_item.key,
            "prompt": data.prompt,
            "visibility": data.visibility,
            "whitelisted_groups": sorted(set(data.whitelisted_groups)),
            "tools": [{"key": item.key, "config": configs[item.key]} for item in tools],
            "workflows": [doc["key"] for doc in workflows],
            "rule_pack_ids": [item.key for item in rule_packs],
        }
        if data.gui_components:
            # Only when declared, so inputs without GUI keep their normalized digest.
            normalized["gui_components"] = [
                component.model_dump(mode="json") for component in data.gui_components
            ]
        resolved = {
            "type": self._ref_meta(type_item),
            "role": {
                **self._ref_meta(role_item),
                "requires_tools": bool(self._role_required_tools(role_item)),
            },
            "personality": self._ref_meta(personality_item),
            "rule_packs": [self._ref_meta(item) for item in rule_packs],
            "tools": [self._ref_meta(item) for item in tools],
            "workflows": [
                {
                    "key": doc["key"],
                    "version": doc["version"],
                    "required_tools": doc["required_tools"],
                }
                for doc in workflows
            ],
            "workflow_tools": sorted(workflow_tools),
            "required_backend_kinds": self._backend_kinds([*tools, *workflow_tools.values()]),
            # Content, not just key@version: a review binds what was actually resolved.
            "fingerprints": {
                "type": content_fingerprint(type_item),
                "role": content_fingerprint(role_item),
                "personality": content_fingerprint(personality_item),
                "rule_packs": {item.key: content_fingerprint(item) for item in rule_packs},
                "tools": {item.key: content_fingerprint(item) for item in tools},
                "workflows": {doc["key"]: content_fingerprint(doc) for doc in workflows},
                "workflow_tools": {
                    key: content_fingerprint(item) for key, item in workflow_tools.items()
                },
            },
        }
        return ValidationResult(
            valid=True,
            resource_type="agent",
            normalized=normalized,
            normalized_digest=validation_digest(
                "agent", normalized, tenant_id=requester.tenant_id, subject_id=requester.id
            ),
            resolved=resolved,
        )

    # ------------------------------------------------------------------
    # References
    # ------------------------------------------------------------------

    def _reference(
        self, kind: str, value: str, path: str, requester: User, issues: _Issues
    ) -> RegistryItem | None:
        """Resolve a registry reference the requester may READ, or record an issue.

        Readability is the existing client catalog policy (``get_info_for_client``):
        tenant/private scope, exposure and nested dependency validity. It is a read
        decision only; it grants neither attachment nor invocation authority.
        """
        registry = self._registries.get(kind)
        try:
            key = registry.resolve_key(value, kind=kind) if registry is not None else ""
            self._catalog.get_info_for_client(kind, key, requester)
        except (KeyError, ValueError):
            issues.add(path, UNAVAILABLE, f"Unknown or unavailable {kind} reference")
            return None
        # Raw (unprojected) metadata drives composition checks below.
        return registry.get_info(key)

    def _references(
        self, kind: str, values: list[str], path: str, requester: User, issues: _Issues
    ) -> list[RegistryItem]:
        resolved: list[RegistryItem] = []
        seen: set[str] = set()
        for index, value in enumerate(values):
            item = self._reference(kind, value, f"{path}.{index}", requester, issues)
            if item is None:
                continue
            if item.key in seen:
                issues.add(f"{path}.{index}", "reference.duplicate", f"Duplicate {kind} reference")
                continue
            seen.add(item.key)
            resolved.append(item)
        return resolved

    # ------------------------------------------------------------------
    # Tools and their agent-specific configuration
    # ------------------------------------------------------------------

    def _tools(
        self, data: AgentCreationInput, requester: User, issues: _Issues
    ) -> tuple[list[RegistryItem], dict[str, dict[str, Any]]]:
        tools: list[RegistryItem] = []
        configs: dict[str, dict[str, Any]] = {}
        for index, ref in enumerate(data.tools):
            path = f"tools.{index}"
            item = self._reference("tool", ref.key, f"{path}.key", requester, issues)
            if item is None:
                continue
            if item.key in configs:
                issues.add(f"{path}.key", "reference.duplicate", "Duplicate tool reference")
                continue
            config = self._tool_config(item, ref.config, f"{path}.config", issues)
            if config is None:
                continue
            configs[item.key] = config
            tools.append(item)
        return tools, configs

    @staticmethod
    def _tool_config(
        item: RegistryItem, config: dict[str, Any], path: str, issues: _Issues
    ) -> dict[str, Any] | None:
        """Validate agent overrides against the registered contract; return them sparse.

        Only supplied keys are returned (coerced), never registry defaults, so later
        default changes are not frozen into the persisted reference. Unknown keys,
        including legacy ``_hidden``-style control flags, are rejected.
        """
        if not config:
            return {}
        contract = _config_contract((item.metadata or {}).get("config_schema"))
        if contract is None:
            issues.add(
                path,
                "tool.config_schema_unavailable",
                "Tool declares no resolvable configuration contract; configuration is rejected",
            )
            return None
        allowed = set(contract.model_fields) | {
            field.alias for field in contract.model_fields.values() if field.alias
        }
        unknown = sorted(set(config) - allowed)
        for key in unknown:
            issues.add(f"{path}.{key}", "tool.config_unknown_field", "Unknown configuration field")
        if unknown:
            return None
        try:
            validated = contract.model_validate(config)
        except ValidationError as exc:
            for error in exc.errors():
                issues.add(_path((path, *error["loc"])), "tool.config_invalid", error["msg"])
            return None
        except (TypeError, ValueError):
            issues.add(path, "tool.config_invalid", "Configuration is invalid")
            return None
        return validated.model_dump(mode="json", include=set(config))

    # ------------------------------------------------------------------
    # Workflows
    # ------------------------------------------------------------------

    @staticmethod
    def _gui(
        data: AgentCreationInput,
        type_item: RegistryItem | None,
        role_item: RegistryItem | None,
        workflows: list[dict[str, Any]],
        issues: _Issues,
    ) -> None:
        """Presentation must match what the agent is and what it declares; never authority."""
        agent_type = type_item.key if type_item else data.type
        role = role_item.key if role_item else data.role_name
        versions = {doc["key"]: doc["version"] for doc in workflows}
        for index, component in enumerate(data.gui_components):
            path = f"gui_components.{index}"
            if component.type == "chat":
                if not supports_v2_conversation(agent_type, role):
                    issues.add(path, "gui.component_incompatible", "Agent has no V2 conversation")
                continue
            source = component.config.source
            if source.kind == "contract":
                if (agent_type, role) != ("form", "enki"):
                    issues.add(
                        f"{path}.config.source",
                        "gui.contract_incompatible",
                        "Contract form is reserved for its approved Agent composition",
                    )
                continue
            key = slugify_workflow_key(source.key)
            if key not in versions:
                issues.add(
                    f"{path}.config.source.key",
                    "gui.workflow_unreferenced",
                    "A form must present one of the Agent's own workflows",
                )
            elif versions[key] != source.version:
                issues.add(
                    f"{path}.config.source.version",
                    "gui.workflow_version_mismatch",
                    "Form workflow version differs from the referenced workflow",
                )

    async def _resolve_workflows(
        self, values: list[str], requester: User, issues: _Issues
    ) -> list[dict[str, Any]]:
        """Readable catalog workflows in input order, with their stored tool sets."""
        readable: list[str] = []
        seen: set[str] = set()
        for index, value in enumerate(values):
            path = f"workflows.{index}"
            key = slugify_workflow_key(value)
            if key in seen:
                issues.add(path, "reference.duplicate", "Duplicate workflow reference")
                continue
            seen.add(key)
            try:
                # Existing requester-aware read policy; missing and unreadable are equal.
                await self._workflows.get_workflow(key, requester)
            except WorkflowNotFoundError:
                issues.add(path, UNAVAILABLE, "Unknown or unavailable workflow reference")
                continue
            readable.append(key)
        if not readable:
            return []
        found, missing = await self._workflows.get_workflows(readable)
        if missing:  # deleted between the two reads: still fail closed
            issues.add("workflows", UNAVAILABLE, "Unknown or unavailable workflow reference")
        return [
            {
                "key": doc.key,
                "version": doc.version,
                "required_tools": sorted({canonicalize(t) for t in doc.required_tools if t}),
            }
            for doc in found
        ]

    def _workflow_tools(
        self, workflows: list[dict[str, Any]], issues: _Issues
    ) -> dict[str, RegistryItem]:
        """Tools attached workflows require. They are server-composed at workflow scope,
        so they must be registered but need not be client-attachable."""
        registry = self._registries.get("tool")
        out: dict[str, RegistryItem] = {}
        for doc in workflows:
            for key in doc["required_tools"]:
                item = registry.get_info(key) if registry is not None else None
                if item is None:
                    issues.add(
                        f"workflows.{doc['key']}",
                        "composition.workflow_tool_unavailable",
                        "Workflow requires a tool that is not registered",
                    )
                    continue
                out[item.key] = item
        return out

    # ------------------------------------------------------------------
    # Composition compatibility
    # ------------------------------------------------------------------

    @staticmethod
    def _role_required_tools(role: RegistryItem) -> set[str]:
        """Same semantics as ``AgentFactory._validate_role_required_tools``."""
        caps = (role.metadata or {}).get("capabilities") or {}
        required = caps.get("required_tools") or {}
        return {canonicalize(entry) for entry in required if entry}

    def _composition(
        self,
        agent_type: RegistryItem | None,
        role: RegistryItem | None,
        tools: list[RegistryItem],
        workflow_tools: Mapping[str, RegistryItem],
        rule_packs: list[RegistryItem],
        issues: _Issues,
    ) -> None:
        if agent_type is None or role is None:
            return
        type_meta, role_meta = agent_type.metadata or {}, role.metadata or {}
        for field in ("agent_group", "agent_subclass"):
            declared = (type_meta.get(field), role_meta.get(field))
            if all(declared) and declared[0] != declared[1]:
                issues.add(
                    "role_name",
                    "composition.role_type_mismatch",
                    f"Role {field} is incompatible with the agent type",
                )

        have = {item.key for item in tools}
        for key in sorted(self._role_required_tools(role) - have):
            issues.add(
                "tools",
                "composition.required_tool_missing",
                f"Role requires tool '{key}'",
            )

        packs = len(rule_packs)
        for item in [*tools, *(workflow_tools[k] for k in sorted(workflow_tools))]:
            minimum = _rulepack_minimum(item.metadata or {})
            if packs < minimum:
                issues.add(
                    "rule_pack_ids",
                    "composition.rulepacks_insufficient",
                    f"Tool '{item.key}' requires at least {minimum} rule pack(s)",
                )

    # ------------------------------------------------------------------
    # Visibility
    # ------------------------------------------------------------------

    @staticmethod
    def _whitelisted_groups(groups: list[str], requester: User, issues: _Issues) -> None:
        """Same rule as ``AgentService._validate_whitelisted_groups``: only own groups."""
        disallowed = set(groups) - agent_access.get_user_groups(requester)
        if disallowed:
            issues.add(
                "whitelisted_groups",
                "visibility.group_not_permitted",
                "Requester may only whitelist groups they belong to",
            )

    # ------------------------------------------------------------------
    # Result helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _ref_meta(item: RegistryItem) -> dict[str, Any]:
        return {"key": item.key, "version": item.version}

    def _backend_kinds(self, items: list[RegistryItem]) -> list[str]:
        """Backend kinds tool classes declare (``ToolManager.validate_specs`` semantics)."""
        registry = self._registries.get("tool")
        kinds: set[str] = set()
        for item in items:
            try:
                cls = registry.get(item.key) if registry is not None else None
            except KeyError:
                continue
            deps = set(getattr(cls, "required_dependencies", []) or []) | set(
                getattr(cls, "required_deps", []) or []
            )
            kinds |= {BACKEND_ALIAS[dep] for dep in deps if dep in BACKEND_ALIAS}
        return sorted(kinds)
