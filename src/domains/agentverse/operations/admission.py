"""Thin adapters from OPS V2 admission ports to SynapticRegistry and UTU.

No new registry or authorization mechanism: capability resolution reuses the
SynapticRegistry's exact-version lookup, and admission authority reuses UTU's permit
and default-deny invoke checks. The output is provenance evidence, never a permit.
"""

from __future__ import annotations

from src.base.events.v2.contracts import EventPermissionError
from src.base.operations.v2.admission import (
    AuthorizationProvenance,
    CapabilityRef,
    UnknownCapability,
    sha256_hex,
)
from src.domains.agentverse.ncsc.models import SynapticExecutionContext
from src.domains.agentverse.registries.synaptic_registry import SynapticRegistry
from src.domains.agentverse.security.utu import AuthorizedAgentPrincipal, UTUAuthority


class SynapticCapabilityCatalog:
    """Exact signal@version resolution; never substitutes another provider version."""

    def __init__(self, registry: SynapticRegistry) -> None:
        self._registry = registry

    def resolve(self, signal: str, version: str) -> CapabilityRef:
        if not isinstance(version, str) or not version.strip():
            raise UnknownCapability("An exact capability version is required")
        try:
            binding = self._registry.resolve(signal, version)
        except (KeyError, ValueError) as exc:
            raise UnknownCapability(f"{signal}@{version}") from exc
        if binding.signal != signal or binding.version != version:
            raise UnknownCapability(f"{signal}@{version}")
        handler = binding.handler
        return CapabilityRef(
            signal=binding.signal,
            version=binding.version,
            provider=binding.provider,
            executable=f"{handler.__module__.removeprefix('src.')}:{handler.__qualname__}",
        )


class UTUAdmissionAuthority:
    """Verify a live UTU principal may invoke a signal, then record evidence of it."""

    def __init__(self, utu: UTUAuthority) -> None:
        self._utu = utu

    async def provenance(
        self, principal: AuthorizedAgentPrincipal, signal: str
    ) -> AuthorizationProvenance:
        permit = principal.permit
        await self._utu.check_permit(permit)
        if (principal.tenant_id, principal.agent_id) != (
            permit.key.tenant_id,
            permit.key.agent_id,
        ):
            raise EventPermissionError("Admission principal does not match its permit")
        # Root grant only: admission is not a nested call and inherits no trusted line.
        self._utu.require_invoke(
            context=SynapticExecutionContext(
                principal_id=principal.agent_id, tenant_id=principal.tenant_id, permit=permit
            ),
            signal=signal,
        )
        key = permit.key
        return AuthorizationProvenance(
            authority="utu",
            tenant_id=key.tenant_id,
            principal_id=key.agent_id,
            subject_id=key.user_id,
            policy_version=permit.versions[0].split(":")[-1],
            evidence_digest=sha256_hex(
                {
                    "tenant": key.tenant_id,
                    "user": key.user_id,
                    "agent": key.agent_id,
                    "session": key.session_id,
                    "versions": list(permit.versions),
                    "signal": signal,
                }
            ),
        )
