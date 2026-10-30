"""First-class Manuscript Tool façade over the existing governed capability.

No engine, repository, actor, permit or grant is constructed here. The trusted adapter
is not a client/API entry point; V1 execution deliberately has no runnable route.
"""

from typing import Any, ClassVar

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.manuscripts.capability import (
    BINDINGS,
    Operation,
    invoke_manuscript,
)
from src.domains.agentverse.ncsc.cpbx import SynapticExchange
from src.domains.agentverse.registries import tool_registry_instance as tool_registry
from src.domains.agentverse.registries.synaptic_registry import SynapticRegistry
from src.domains.agentverse.registries.tool_registry import ToolRegistry
from src.domains.agentverse.security.utu import AuthorizedAgentPrincipal, UTUAuthority
from src.domains.agentverse.tools.base import BaseTool
from src.domains.agentverse.tools.manuscript_tool.spec import (
    ManuscriptToolConfig,
    manuscript_tool,
)
from src.domains.agentverse.tools.spec import SynapticToolSpec


@tool_registry.bind_spec(manuscript_tool)
class ManuscriptTool(BaseTool):
    key = "manuscript"
    name = "Manuscript"
    version = "1.0.0"
    parameters: ClassVar[dict[str, Any]] = {}

    def __init__(self, config: ManuscriptToolConfig | dict[str, Any] | None = None) -> None:
        # BaseTool's execution settings are inert: execute/_run always deny. Keep
        # the strict resource preference separate from its permissive V1 ToolConfig.
        super().__init__()
        self._manuscript_config = ManuscriptToolConfig.model_validate(
            {} if config is None else config
        )

    async def execute(self, socket_emitter: Any = None, **kwargs: Any) -> Any:
        raise EventPermissionError("Manuscript requires narrowed UTU/CPBX invocation; V1 is denied")

    async def _run(self, socket_emitter: Any = None, **params: Any) -> Any:
        raise EventPermissionError("Manuscript has no direct execution path")

    async def invoke(
        self,
        *,
        authority: UTUAuthority,
        exchange: SynapticExchange,
        principal: AuthorizedAgentPrincipal,
        op: Operation,
        command: dict[str, Any],
    ) -> dict[str, Any]:
        """Trusted runtime adapter; configuration can only fill a start preference."""
        payload = dict(command)
        if op == "start" and "manuscript" not in payload:
            payload["manuscript"] = self._manuscript_config.default_manuscript
        return await invoke_manuscript(authority, exchange, principal, op, payload)


def validate_manuscript_bindings(
    registry: SynapticRegistry, *, tools: ToolRegistry = tool_registry
) -> None:
    """Fail closed at trusted startup when Manuscript execution is policy-enabled.

    Check the actual registered Tool metadata, not merely a parallel constant. A
    descriptor may exist without enabled providers; registration itself grants nothing.
    """
    item = tools.get_info("manuscript")
    if item is None:
        raise ValueError("Manuscript Tool registration is missing")
    declared = SynapticToolSpec.model_validate(item.metadata.get("synaptic") or {})
    expected = {binding.signal: binding for binding in BINDINGS.values()}
    actions = declared.capabilities
    if len(actions) != len(expected) or {action.signal for action in actions} != set(expected):
        raise ValueError("Manuscript Tool must declare exactly its six existing actions")
    for action in actions:
        pinned = expected[action.signal]
        input_contract = f"caos.manuscript_session.{action.signal.rsplit('.', 1)[-1]}@1"
        if (
            action.provider != pinned.provider
            or action.version != pinned.version
            or action.access != "granted"
            or action.trusted_lines
            or action.input_contract != input_contract
            or action.output_contract != "caos.manuscript_session_result@1"
        ):
            raise ValueError(f"Manuscript action declaration mismatch: {action.signal}")
        try:
            binding = registry.resolve(action.signal, action.version)
        except KeyError as exc:
            raise ValueError(f"Missing exact Manuscript binding: {action.signal}") from exc
        if binding.provider != action.provider or binding.version != action.version:
            raise ValueError(f"Manuscript binding provider/version mismatch: {action.signal}")
