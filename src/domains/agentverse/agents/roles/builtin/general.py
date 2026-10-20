from src.domains.agentverse.agents.roles.builtin.spec import role_spec
from src.domains.agentverse.registries import role_registry_instance as role_registry


@role_registry.bind_spec(role_spec)
class GeneralRole:
    pass
