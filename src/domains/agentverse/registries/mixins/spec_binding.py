# src/domains/agentverse/registries/mixins/spec_binding.py
from __future__ import annotations

from collections.abc import Callable
from typing import Generic, TypeVar

from pydantic import BaseModel

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)

T = TypeVar("T")
SpecT = TypeVar("SpecT", bound=BaseModel)


class SpecBindingMixin(Generic[SpecT]):
    """
    Adds spec-based component registration to a Registry.

    The base Registry remains the single source of truth.

    A spec is translated directly into the normal Registry registration
    contract:

        key         <- spec.key
        name        <- spec.name
        description <- spec.description
        version     <- spec.version
        metadata    <- full serialized spec

    The Python component class is the registered implementation and does
    not affect the spec's canonical name or key.
    """

    def bind_spec(
        self,
        spec: SpecT,
    ) -> Callable[[type[T]], type[T]]:
        """
        Bind a spec-described component to the registry.

        The complete spec is stored as registry metadata so consumers can
        recover the full declarative definition from the normal registry
        entry without maintaining a separate spec store.
        """

        AGENTVERSE_LOGGER.debug(
            LogDomain.NERV_HQ,
            "[🧬 REGISTRY][🔗 SPEC] bind_requested",
            extra={
                "registry": self.__class__.__name__,
                "spec_name": getattr(spec, "name", None),
                "spec_key": getattr(spec, "key", None),
            },
        )

        def _decorator(cls: type[T]) -> type[T]:
            self.register(  # type: ignore[attr-defined]
                name=spec.name,  # type: ignore[attr-defined]
                description=getattr(spec, "description", None),
                version=getattr(spec, "version", "1.0.0"),
                metadata=spec.model_dump(mode="json"),
                key=spec.key,  # type: ignore[attr-defined]
            )(cls)

            return cls

        return _decorator