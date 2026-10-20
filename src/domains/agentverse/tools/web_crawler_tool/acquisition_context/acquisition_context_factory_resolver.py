from __future__ import annotations

from src.domains.agentverse.registries.registries import (
    tool_runtime_dependency_registry_instance as tool_runtime_dependency_registry,
)


@tool_runtime_dependency_registry.register(
    key="acquisition_context_factory",
    name="Crawler Acquisition Context Factory",
)
def resolve_acquisition_context_factory(request):
    return (
        request.app.container.agentverse
        .web_crawler()
        .acquisition_context_factory()
    )