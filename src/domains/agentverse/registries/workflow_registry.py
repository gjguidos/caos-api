from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.domains.agentverse.exceptions import RegistrationError
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.registries.base import Registry
from src.domains.agentverse.registries.utils.register_registry import register_registry
from src.domains.agentverse.workflows.helpers.serialize import _serialize_workflow
from src.domains.agentverse.workflows.spec import WorkflowSpec


@register_registry(name="Workflow Registry", icon="🧭")
class WorkflowRegistry(Registry[WorkflowSpec]):
    """
    Registry for deterministic workflow definitions used by OrchestrationTool.

    Stores the full ``WorkflowSpec`` as the entry, with the FE-friendly serialized
    form as the item metadata (catalog/listing).
    """

    name = "workflow"
    description = "Registry for orchestration workflow definitions"
    version = "1.0.0"

    def __init__(self) -> None:
        super().__init__(name=self.name)

        AGENTVERSE_LOGGER.info(
            LogDomain.NERV_HQ,
            f"[🧭 REGISTRY][⚙️ WORKFLOW] initialized name={self.name}",
            extra={
                "registry_name": self.name,
                "version": self.version,
            },
        )

    def _construct_key(self, key: str) -> str:
        return (key or "").strip().lower().replace(" ", "_")

    def register(
        self,
        *,
        key: str,
        name: str,
        description: str | None = None,
        version: str = "0.1.0",
        metadata: dict[str, Any] | None = None,
        workflow_factory: Callable[[], WorkflowSpec] | None = None,
    ) -> Callable[[Callable[[], WorkflowSpec]], Callable[[], WorkflowSpec]]:
        reg_key = self._construct_key(key)

        def decorator(factory: Callable[[], WorkflowSpec]) -> Callable[[], WorkflowSpec]:
            spec = factory()
            if not isinstance(spec, WorkflowSpec):
                AGENTVERSE_LOGGER.error(
                    LogDomain.NERV_HQ,
                    f"[🧭 REGISTRY][💥 WORKFLOW] invalid_factory_return key={reg_key}",
                    extra={
                        "registry_name": self._name,
                        "workflow_name": name,
                        "workflow_key": reg_key,
                        "returned_type": type(spec).__name__,
                    },
                )
                raise TypeError(
                    "WorkflowRegistry.register expects a factory returning WorkflowSpec"
                )

            if not spec.key:
                spec.key = reg_key
            if not spec.name:
                spec.name = name
            if description and not spec.description:
                spec.description = description
            if version:
                spec.version = version

            self._register_workflow_spec(
                spec,
                key=reg_key,
                version=version,
                metadata=metadata,
            )
            return factory

        return decorator(workflow_factory) if workflow_factory else decorator

    def _register_workflow_spec(
        self,
        spec: WorkflowSpec,
        *,
        key: str | None = None,
        version: str = "0.1.0",
        metadata: dict[str, Any] | None = None,
        replace: bool = False,
    ) -> WorkflowSpec:
        key = key or self._construct_key(spec.key)

        if not replace and key in self._items:
            AGENTVERSE_LOGGER.warning(
                LogDomain.NERV_HQ,
                f"[🧭 REGISTRY][⚠️ WORKFLOW] duplicate_registration_blocked name={spec.name}",
                extra={
                    "registry_name": self._name,
                    "workflow_name": spec.name,
                    "workflow_key": key,
                    "version": version,
                },
            )
            raise RegistrationError(f"Workflow '{key}' already registered")

        self._store_value(key, spec)

        wf_meta = _serialize_workflow(spec)
        if metadata:
            wf_meta.update(metadata)

        self._store_item(
            key,
            key=key,
            name=spec.name,
            description=spec.description,
            version=version,
            metadata=wf_meta,
        )
        self.registration_count += 1

        return spec

    def get(self, key: str, version: str | None = None) -> WorkflowSpec:
        k = self._construct_key(key)

        AGENTVERSE_LOGGER.info(
            LogDomain.NERV_HQ,
            f"[🧭 REGISTRY][🔎 WORKFLOW] lookup_requested name={key} key={k}",
            extra={
                "registry_name": self._name,
                "workflow_name": key,
                "workflow_key": k,
                "requested_version": version,
            },
        )

        if k not in self._registry:
            AGENTVERSE_LOGGER.warning(
                LogDomain.NERV_HQ,
                f"[🧭 REGISTRY][❌ WORKFLOW] lookup_failed_missing name={key} key={k}",
                extra={
                    "registry_name": self._name,
                    "workflow_name": key,
                    "workflow_key": k,
                },
            )
            raise KeyError(f"Workflow '{key}' not found in {self._name} registry")

        actual_version = self._items[k].version
        if version and actual_version != version:
            AGENTVERSE_LOGGER.warning(
                LogDomain.NERV_HQ,
                f"[🧭 REGISTRY][⚠️ WORKFLOW] version_mismatch name={key} key={k}",
                extra={
                    "registry_name": self._name,
                    "workflow_name": key,
                    "workflow_key": k,
                    "requested_version": version,
                    "actual_version": actual_version,
                },
            )
            raise KeyError(f"Workflow '{key}' version {version} not found (found {actual_version})")

        spec = self._registry[k]

        AGENTVERSE_LOGGER.info(
            LogDomain.NERV_HQ,
            f"[🧭 REGISTRY][📦 WORKFLOW] lookup_success name={spec.name} key={k}",
            extra={
                "registry_name": self._name,
                "workflow_name": spec.name,
                "workflow_key": k,
                "version": actual_version,
                "tag_count": len(spec.tags or []),
                "has_pause": bool(self._items[k].metadata.get("has_pause")),
            },
        )

        return spec
