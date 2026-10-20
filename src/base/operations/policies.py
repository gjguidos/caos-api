from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

PolicyProvider = Callable[[], object]


@dataclass(frozen=True, slots=True)
class JobPolicyDefinition:
    producer: str
    policy_type: type[Any]
    provider: PolicyProvider | None = None


_POLICY_REGISTRY: dict[str, JobPolicyDefinition] = {}


def job_policy(*, producer: str):
    def decorator(policy_type: type[Any]) -> type[Any]:
        if producer in _POLICY_REGISTRY:
            raise RuntimeError(
                f"Job policy already registered: {producer}"
            )

        _POLICY_REGISTRY[producer] = JobPolicyDefinition(
            producer=producer,
            policy_type=policy_type,
        )

        setattr(
            policy_type,
            "__job_producer__",
            producer,
        )

        return policy_type

    return decorator


def register_policy_provider(
    *,
    producer: str,
    provider: PolicyProvider,
) -> None:
    definition = _POLICY_REGISTRY.get(producer)

    if definition is None:
        raise KeyError(
            f"No job policy registered for producer: {producer}"
        )

    _POLICY_REGISTRY[producer] = JobPolicyDefinition(
        producer=definition.producer,
        policy_type=definition.policy_type,
        provider=provider,
    )


def registered_policies() -> Mapping[str, JobPolicyDefinition]:
    return dict(_POLICY_REGISTRY)