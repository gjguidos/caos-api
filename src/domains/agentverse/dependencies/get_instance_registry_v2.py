from fastapi import Request, WebSocket

from src.domains.agentverse.runtime.instances_v2 import AgentInstanceRegistryV2


def get_instance_registry(request: Request | WebSocket) -> AgentInstanceRegistryV2:
    """The single way to reach the runtime agent registry from a request or socket.

    The registry is mounted on the container namespace as an already-resolved instance,
    not as a provider (see `dependencies/di_container.py`). Going through here keeps that
    detail in one place - call sites that reproduce the attribute walk have historically
    drifted into calling the instance as if it were a provider.
    """
    return request.app.container.agentverse.instance_registry_v2
