from src.domains.agentverse.agents.roles.enki.spec import role_spec
from src.domains.agentverse.registries import role_registry_instance


@role_registry_instance.bind_spec(role_spec)
class EnkiRole:
    """Declarative role registration; no orchestration handlers or permission grants."""
