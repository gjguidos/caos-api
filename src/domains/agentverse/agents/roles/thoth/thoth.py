from src.domains.agentverse.agents.roles.thoth.spec import role_spec
from src.domains.agentverse.registries import role_registry_instance


@role_registry_instance.bind_spec(role_spec)
class ThothRole:
    """Declarative role registration; no orchestration handlers or permission grants."""
