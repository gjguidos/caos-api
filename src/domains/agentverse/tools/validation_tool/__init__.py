"""``data.validation``: governed, deterministic, read-only validation of creation inputs.

CPBX/UTU decide who may invoke it (registration grants nothing). The requester is the
verified subject of the CPBX-issued execution permit, never a payload field. The tool
has no side effects: it does not persist, construct agents, call an LLM, provision
grants or approvals, and a valid result is never execution authorization.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from src.base.entities.user import User
from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.logging.logger_v1 import AGENTVERSE_LOGGER, LogDomain
from src.domains.agentverse.ncsc.models import Synapse
from src.domains.agentverse.registries.synaptic_registry import SynapticBinding, SynapticRegistry
from src.domains.agentverse.security.revocation import ExecutionPermit
from src.domains.agentverse.tools.base import ToolResult
from src.domains.agentverse.tools.validation_tool.models import (
    ResourceValidator,
    ValidationIssue,
    ValidationRequest,
    ValidationResult,
)
from src.domains.agentverse.tools.validation_tool.spec import (
    DATA_VALIDATION_PROVIDER,
    DATA_VALIDATION_SIGNAL,
    DATA_VALIDATION_TOOL_SPEC,
    DATA_VALIDATION_VERSION,
)

if TYPE_CHECKING:
    from src.domains.agentverse.ncsc.cpbx import AuthorizedSynapticClient
    from src.domains.agentverse.security.utu import UTUAuthority


class DataValidationTool:
    def __init__(
        self, *, validators: Mapping[str, ResourceValidator], authority: UTUAuthority
    ) -> None:
        self._validators = validators
        self._authority = authority

    async def validate(self, synapse: Synapse, client: AuthorizedSynapticClient) -> dict[str, Any]:
        requester = await self._requester(synapse, client)
        result = await self._validate(synapse.payload, requester)
        AGENTVERSE_LOGGER.info(
            LogDomain.EXISTENTIAL_INDEX,
            "[🧠 AEI][🧪 VALIDATE] data_validation",
            extra={
                "resource_type": result.resource_type,
                "valid": result.valid,
                "issue_codes": sorted({issue.code for issue in result.issues}),
                "tenant_id": synapse.tenant_id,
                "principal_id": synapse.source,
                "provider": synapse.target,
                "provider_version": synapse.provider_version,
                "entry_operation": synapse.entry_operation,
            },
        )
        return ToolResult(success=True, result=result.model_dump(mode="json")).model_dump()

    async def _validate(self, payload: Any, requester: User) -> ValidationResult:
        try:
            request = ValidationRequest.model_validate(payload)
        except ValidationError as exc:
            unsupported = any(e["loc"][:1] == ("resource_type",) for e in exc.errors())
            issue = (
                ValidationIssue(
                    path="resource_type",
                    code="resource_type.unsupported",
                    message="Unsupported resource type",
                )
                if unsupported
                else ValidationIssue(
                    path="", code="request.invalid", message="Invalid data.validation request"
                )
            )
            return ValidationResult(valid=False, resource_type=None, issues=[issue])
        validator = self._validators.get(request.resource_type)
        if validator is None:
            return ValidationResult(
                valid=False,
                resource_type=None,
                issues=[
                    ValidationIssue(
                        path="resource_type",
                        code="resource_type.unsupported",
                        message="Unsupported resource type",
                    )
                ],
            )
        return await validator.validate(request.payload, requester=requester)

    async def _requester(self, synapse: Synapse, client: AuthorizedSynapticClient) -> User:
        """The verified permit subject; missing or stale authority denies."""
        permit = client.execution_permit
        if not isinstance(permit, ExecutionPermit) or (
            permit.key.tenant_id,
            permit.key.agent_id,
        ) != (synapse.tenant_id, synapse.source):
            raise EventPermissionError("data.validation requires a UTU execution permit")
        if permit.scope and (
            not permit.binding_chain
            or permit.binding_chain[-1].signal != DATA_VALIDATION_SIGNAL
            or permit.binding_chain[-1].provider != DATA_VALIDATION_PROVIDER
            or permit.binding_chain[-1].version != DATA_VALIDATION_VERSION
            or synapse.provider_version != DATA_VALIDATION_VERSION
        ):
            raise EventPermissionError("data.validation requires its scoped CPBX invocation")
        return await self._authority.resource_requester(permit)


def register_data_validation(
    registry: SynapticRegistry, tool: DataValidationTool
) -> SynapticBinding:
    """Trusted bootstrap registration. Registration grants no invocation permission."""
    return registry.register(
        DATA_VALIDATION_SIGNAL,
        description="Deterministic, read-only validation of a resource creation input",
        version=DATA_VALIDATION_VERSION,
        metadata={
            "access": "system",
            "deterministic": True,
            "side_effects": False,
            "synaptic": DATA_VALIDATION_TOOL_SPEC.model_dump(),
        },
        component=SynapticBinding(
            DATA_VALIDATION_SIGNAL,
            DATA_VALIDATION_PROVIDER,
            tool.validate,
            DATA_VALIDATION_VERSION,
        ),
    )
