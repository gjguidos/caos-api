"""``caos.nataraja.manifest.v1``: the versioned desired state of a development environment.

The manifest declares expectations only. It grants nothing, provisions nothing and is
never used to construct an Agent: creation stays with Genesis, activation with Khepri.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from src.base.operations.v2.admission import sha256_hex

MANIFEST_CONTRACT = "caos.nataraja.manifest.v1"
DEFAULT_MANIFEST = "config/nataraja.local.json"

Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")]
# required: absence is a failure. optional: absence is acceptable. deferred: absence is
# an expected, not-yet-executed lifecycle step. manual: managed by humans (G3.4).
Requirement = Literal["required", "optional", "deferred", "manual"]
ServiceName = Literal["UTU", "CPBX", "Khepri", "Seshat"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CitizenExpectation(_Strict):
    """A deterministic System Citizen: a policy-registered principal (no Agent document)."""

    name: Identifier
    principal_id: Identifier
    requirement: Literal["required", "optional"]


class ServiceExpectation(_Strict):
    """A trusted runtime service (DI-owned). Not a principal and never an Agent."""

    name: ServiceName
    requirement: Literal["required", "optional"]


class GuiExpectation(_Strict):
    key: Identifier
    type: Literal["form", "chat"]
    default: bool


class CreationExpectation(_Strict):
    via: Literal["genesis_bootstrap", "genesis_request"]
    slot: Identifier
    # Policy principal that initiates the request (JOSHUA for bootstrap handoffs).
    initiator: Identifier
    signal: Identifier


class LifecycleExpectation(_Strict):
    """Genesis owns creation, Khepri owns activation: creation persists the Agent
    sleeping and activation is a separate, separately authorized operation."""

    after_creation: Literal["sleeping"]
    activation: Literal["khepri_independent"]
    activation_operation: Literal["agent.lifecycle.activate"]


class AgentExpectation(_Strict):
    name: Identifier
    agent_id: Identifier
    requirement: Requirement
    type: Literal["chat", "form"]
    role_name: Identifier
    personality_key: Identifier
    runtime_version: Literal["v1", "v2"]
    creation: CreationExpectation
    gui: tuple[GuiExpectation, ...] = Field(min_length=1)
    lifecycle: LifecycleExpectation

    @model_validator(mode="after")
    def _gui(self) -> Self:
        if sum(component.default for component in self.gui) != 1:
            raise ValueError("Exactly one primary GUI component is required")
        if len({component.key for component in self.gui}) != len(self.gui):
            raise ValueError("GUI component keys must be unique")
        return self


class EnvironmentManifest(_Strict):
    contract: Literal["caos.nataraja.manifest.v1"]
    revision: StrictInt = Field(ge=1)
    environment: Identifier
    tenant_id: Identifier
    owner_id: Identifier
    system_citizens: tuple[CitizenExpectation, ...]
    system_services: tuple[ServiceExpectation, ...]
    agents: tuple[AgentExpectation, ...]
    # Agents created by governed manual creation (G3.4) are legitimate, not drift.
    manual_agents: Literal["allowed", "forbidden"]

    @model_validator(mode="after")
    def _unique(self) -> Self:
        for label, values in (
            ("citizen", [c.principal_id for c in self.system_citizens]),
            ("citizen name", [c.name for c in self.system_citizens]),
            ("service", [s.name for s in self.system_services]),
            ("agent", [a.agent_id for a in self.agents]),
            ("agent name", [a.name for a in self.agents]),
            ("slot", [a.creation.slot for a in self.agents]),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"Duplicate {label} in manifest")
        if {c.principal_id for c in self.system_citizens} & {a.agent_id for a in self.agents}:
            raise ValueError("A System Citizen is not an Agent document")
        return self

    @property
    def digest(self) -> str:
        return sha256_hex(self.model_dump(mode="json"))


def load_manifest(path: str | Path = DEFAULT_MANIFEST) -> EnvironmentManifest:
    return EnvironmentManifest.model_validate_json(Path(path).read_text())


def manifest_text(manifest: EnvironmentManifest) -> str:
    return json.dumps(manifest.model_dump(mode="json"), indent=2) + "\n"
