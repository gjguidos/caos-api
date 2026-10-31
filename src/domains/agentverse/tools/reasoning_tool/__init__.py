"""First-class Reasoning Tool façade over the governed ``cognitive.interpret`` capability.

No engine, task registry, model client, permit or grant is constructed here: the existing
``ReasoningExecutor`` behind ``system.reasoning`` is the only Reasoning engine. The trusted
adapter is not a client/API entry point; V1 execution deliberately has no runnable route.
"""

from typing import Any, ClassVar

from pydantic import ValidationError

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.ncsc.cpbx import SynapticExchange
from src.domains.agentverse.reasoning.capability import (
    PROVIDER,
    SIGNAL,
    VERSION,
    interpret,
    validate_reasoning_binding,
)
from src.domains.agentverse.reasoning.models import (
    REQUEST_CONTRACT,
    RESULT_CONTRACT,
    ReasoningFailure,
    ReasoningRequest,
    ReasoningResult,
)
from src.domains.agentverse.registries import tool_registry_instance as tool_registry
from src.domains.agentverse.registries.synaptic_registry import SynapticRegistry
from src.domains.agentverse.registries.tool_registry import ToolRegistry
from src.domains.agentverse.security.utu import AuthorizedAgentPrincipal, UTUAuthority
from src.domains.agentverse.tools.base import BaseTool
from src.domains.agentverse.tools.reasoning_tool.spec import (
    ReasoningToolConfig,
    reasoning_tool,
)
from src.domains.agentverse.tools.spec import SynapticToolSpec


@tool_registry.bind_spec(reasoning_tool)
class ReasoningTool(BaseTool):
    key = "reasoning"
    name = "Reasoning"
    version = "1.0.0"
    parameters: ClassVar[dict[str, Any]] = {}

    def __init__(self, config: ReasoningToolConfig | dict[str, Any] | None = None) -> None:
        # BaseTool's execution settings are inert: execute/_run always deny.
        super().__init__()
        self._reasoning_config = ReasoningToolConfig.model_validate(
            {} if config is None else config
        )

    async def execute(self, socket_emitter: Any = None, **kwargs: Any) -> Any:
        raise EventPermissionError("Reasoning requires narrowed UTU/CPBX invocation; V1 is denied")

    async def _run(self, socket_emitter: Any = None, **params: Any) -> Any:
        raise EventPermissionError("Reasoning has no direct execution path")

    async def invoke(
        self,
        *,
        authority: UTUAuthority,
        exchange: SynapticExchange,
        principal: AuthorizedAgentPrincipal,
        request: dict[str, Any],
        deadline: float | None = None,
    ) -> dict[str, Any]:
        """Trusted runtime adapter. Configuration can only narrow the requestable tasks.

        The request is the strict, identity-free ``caos.reasoning.request``: model,
        provider, instructions, grant or scope fields are rejected before any call.
        """
        try:
            parsed = ReasoningRequest.model_validate(request)
        except ValidationError:
            return _failed("reasoning.invalid_request")
        if parsed.task not in self._reasoning_config.tasks:
            return _failed("reasoning.task_unavailable")
        return await interpret(authority, exchange, principal, request, deadline=deadline)


def _failed(code: Any) -> dict[str, Any]:
    return ReasoningResult(
        status="failed", failure=ReasoningFailure(code=code, retryable=False)
    ).model_dump(mode="json")


def validate_reasoning_tool(
    registry: SynapticRegistry, *, tools: ToolRegistry = tool_registry
) -> None:
    """Fail closed at trusted startup when Reasoning execution is policy-enabled.

    Check the actual registered Tool metadata and the exact provider binding. The
    descriptor may exist without an enabled provider; registration itself grants nothing.
    """
    item = tools.get_info("reasoning")
    if item is None:
        raise ValueError("Reasoning Tool registration is missing")
    declared = SynapticToolSpec.model_validate(item.metadata.get("synaptic") or {})
    if len(declared.capabilities) != 1:
        raise ValueError("Reasoning Tool must declare exactly cognitive.interpret")
    (action,) = declared.capabilities
    if (
        action.signal != SIGNAL
        or action.provider != PROVIDER
        or action.version != VERSION
        or action.access != "granted"
        or action.trusted_lines
        or action.input_contract != f"{REQUEST_CONTRACT}@1"
        or action.output_contract != f"{RESULT_CONTRACT}@1"
    ):
        raise ValueError("Reasoning action declaration mismatch")
    validate_reasoning_binding(registry)
