"""Immutable attenuation of an existing UTU permit, never a grant or wire credential."""

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CapabilityBinding:
    signal: str
    provider: str
    version: str

    def __post_init__(self) -> None:
        if not isinstance(self.signal, str) or not re.fullmatch(
            r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+", self.signal
        ):
            raise ValueError("An exact synaptic signal is required")
        if (
            not isinstance(self.provider, str)
            or not self.provider.strip()
            or self.provider != self.provider.strip()
        ):
            raise ValueError("An exact provider is required")
        if not isinstance(self.version, str) or not re.fullmatch(
            r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?", self.version
        ):
            raise ValueError("An exact provider version is required")


@dataclass(frozen=True, slots=True)
class CapabilityScope:
    operation: str
    entry: CapabilityBinding
    bindings: frozenset[CapabilityBinding]

    def __post_init__(self) -> None:
        if not isinstance(self.operation, str) or not re.fullmatch(
            r"[a-z][a-z0-9_.]{0,127}", self.operation
        ):
            raise ValueError("An explicit entry operation is required")
        if (
            type(self.entry) is not CapabilityBinding
            or type(self.bindings) is not frozenset
            or not self.bindings
            or len(self.bindings) > 64
            or any(type(binding) is not CapabilityBinding for binding in self.bindings)
            or self.entry not in self.bindings
        ):
            raise ValueError("Invalid immutable capability restrictions")

    def require_subset(self, parent: "CapabilityScope") -> None:
        if (
            self.operation != parent.operation
            or self.entry != parent.entry
            or not self.bindings <= parent.bindings
        ):
            raise ValueError("Capability scope cannot be widened or change entry operation")
