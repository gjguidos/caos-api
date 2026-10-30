"""Deterministic V2 Agent Composition: ``data.validation`` result → ``store.create`` payload.

Pure and in-process: no persistence, activation, LLM calls, grants or registry writes.
It derives only non-authority ``Agent`` fields; identity, ownership, tenant, creator
and exposure remain ``store.create``'s to derive from its own trusted write context.

Trust: the validation result must come from ``data.validation`` within the same
trusted execution. Its digest is re-verified against this context, so a tampered
result, or one validated for another requester, is rejected.
"""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from pydantic import ValidationError

from src.base.infrastructure.ai.client_factory import ClientFactory
from src.domains.agentverse.agents.composition.models import (
    CompositionContext,
    CompositionIssue,
    CompositionResult,
    V2CompositionPolicy,
)
from src.domains.agentverse.agents.conversational_composition_v2 import supports_v2_conversation
from src.domains.agentverse.agents.factory import AgentFactory
from src.domains.agentverse.entities.gui_components import default_gui_components
from src.domains.agentverse.services.agent_service import AgentService
from src.domains.agentverse.tools.validation_tool.models import (
    AgentCreationInput,
    ValidationResult,
    validation_digest,
)

# What the V2 runtime calls on a backend of this kind (``CognitiveResponseV2``).
_RUNTIME_PROBES = {"llm": "get_response_async"}


class AgentComposer:
    def __init__(
        self,
        *,
        catalog: Callable[[], ClientFactory | None],
        factory_rules: AgentFactory | None = None,
    ) -> None:
        # ``catalog`` returns the running backend catalog (``app.state.client_factory``),
        # the same source the V2 runtime resolves models from.
        self._catalog = catalog
        self._rules = factory_rules or AgentFactory()

    def compose(
        self,
        validation: ValidationResult,
        *,
        context: CompositionContext,
        policy: V2CompositionPolicy,
    ) -> CompositionResult:
        issues: list[CompositionIssue] = []

        def fail(path: str, code: str, message: str) -> CompositionResult:
            issues.append(CompositionIssue(path=path, code=code, message=message))
            return CompositionResult(composed=False, issues=sorted(issues, key=_order))

        if not isinstance(context, CompositionContext) or not isinstance(
            policy, V2CompositionPolicy
        ):
            return fail("", "composition.untrusted_context", "Trusted context and policy required")
        if not isinstance(validation, ValidationResult):
            return fail(
                "validation", "validation.untrusted", "A data.validation result is required"
            )
        if not (
            validation.valid
            and validation.resource_type == "agent"
            and validation.normalized is not None
            and validation.resolved is not None
            and validation.normalized_digest
        ):
            return fail("validation", "validation.not_valid", "Validation did not succeed")
        expected = validation_digest(
            "agent",
            validation.normalized,
            tenant_id=context.tenant_id,
            subject_id=context.subject_id,
        )
        if validation.normalized_digest != expected:
            return fail(
                "validation.normalized_digest",
                "validation.digest_mismatch",
                "Validation result was altered or validated for another requester",
            )
        try:
            # Re-parse with the strict creation contract: server-owned or unknown
            # fields can never ride along in a normalized input.
            data = AgentCreationInput.model_validate(validation.normalized)
        except ValidationError:
            return fail("validation.normalized", "validation.not_valid", "Normalized input invalid")

        resolved = validation.resolved
        if data.type not in policy.agent_types or not supports_v2_conversation(
            data.type, data.role_name
        ):
            issues.append(
                CompositionIssue(
                    path="type",
                    code="composition.type_unsupported",
                    message="Agent type is not supported by the V2 runtime",
                )
            )
        if (resolved.get("role") or {}).get("requires_tools") and not policy.role_tools_supported:
            issues.append(
                CompositionIssue(
                    path="role_name",
                    code="composition.role_tools_unsupported",
                    message="Role requires tools, which the V2 runtime does not execute",
                )
            )
        backends = self._backends(
            set(resolved.get("required_backend_kinds") or ()) | set(policy.required_backends),
            issues,
        )
        if issues:
            return CompositionResult(composed=False, issues=sorted(issues, key=_order))

        candidate: dict[str, Any] = {
            "runtime_version": policy.runtime_version,
            "name": data.name,
            "system_name": self._rules._to_system_name(data.name),
            "type": data.type,
            "role_name": data.role_name,
            "personality_key": data.personality_key,
            "prompt": data.prompt,
            # V1's chat route is ``/chat/{id}``; the id is assigned by store.create and
            # the V2 runtime has no V1 chat route (same as the V2 Genesis seed).
            "chat_url": "",
            # V1 rule (name, type, creator); creator is the permit subject that
            # store.create will also record as ``creator``.
            "dna_sequence": self._rules._generate_dna_sequence(
                SimpleNamespace(name=data.name, type=data.type, user_id=context.subject_id)
            ),
            **backends,
            "tools": [tool.model_dump(mode="json") for tool in data.tools],
            "workflows": list(data.workflows),
            "rule_pack_ids": list(data.rule_pack_ids),
            "visibility": data.visibility,
            "whitelisted_groups": list(data.whitelisted_groups),
            # Declared presentation, or the reviewed default for this composition.
            "gui_components": [
                component.model_dump(mode="json")
                for component in (
                    data.gui_components
                    or default_gui_components(
                        agent_type=data.type,
                        role_name=data.role_name,
                        runtime_version=policy.runtime_version,
                    )
                )
            ],
        }
        return CompositionResult(
            composed=True,
            candidate=candidate,
            validation_digest=validation.normalized_digest,
        )

    def _backends(self, required: set[str], issues: list[CompositionIssue]) -> dict[str, str]:
        """Backend selectors with V1's rule (``AgentService._fill_missing_backends`` +
        ``_validate_component_types_helper``): every selector takes the catalog default
        or ``""``; a selector must name a registered backend; required kinds must be
        set and, unlike V1, must be real (non-noop) and runtime-capable."""
        factory = self._catalog()
        if not isinstance(factory, ClientFactory):
            issues.append(
                CompositionIssue(
                    path="backends",
                    code="composition.backend_catalog_unavailable",
                    message="Backend catalog is unavailable",
                )
            )
            return {}
        selectors: dict[str, str] = {}
        for field, kind in AgentService.COMPONENT_MAPPING.items():
            value = factory.defaults.get(kind) or ""
            repo = factory.get_repo(kind, value) if value else None
            probe = _RUNTIME_PROBES.get(kind)
            usable = (
                repo is not None
                and not getattr(repo, "is_noop", False)
                and (probe is None or callable(getattr(repo, probe, None)))
            )
            if value and repo is None:
                issues.append(
                    CompositionIssue(
                        path=f"backends.{kind}",
                        code="composition.backend_unregistered",
                        message="Default backend is not registered",
                    )
                )
            elif kind in required and not usable:
                issues.append(
                    CompositionIssue(
                        path=f"backends.{kind}",
                        code="composition.backend_unavailable",
                        message="Required backend provider is unavailable",
                    )
                )
            selectors[field] = value
        for kind in sorted(required - set(AgentService.COMPONENT_MAPPING.values())):
            issues.append(
                CompositionIssue(
                    path=f"backends.{kind}",
                    code="composition.backend_unavailable",
                    message="Required backend kind has no Agent selector",
                )
            )
        return selectors


def _order(issue: CompositionIssue) -> tuple[str, str, str]:
    return issue.path, issue.code, issue.message
