from __future__ import annotations

from typing import Any

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.registries import tool_registry_instance as tool_registry
from src.domains.agentverse.tools.base import BaseTool, ToolConfig, ToolExecutionError
from src.domains.agentverse.tools.green_claim_tool.spec import green_claim_tool
from src.domains.green_claim.services.green_claim_service import GreenClaimService


@tool_registry.bind_spec(green_claim_tool)
class GreenClaimTool(BaseTool):
    """
    Tool for analyzing text for greenwashing and environmental compliance violations.

    Uses the GreenClaimService to identify green claims and check them against
    environmental marketing principles. Supports chunked analysis for long texts
    and optional second-opinion verification for higher confidence.
    """

    name = "Green Claim Analyzer"
    key = "green_claim"
    version = "1.0.0"

    parameters = {
        "text": {
            "type": "string",
            "description": "Text to analyze for greenwashing and environmental claims",
            "required": True,
        }
    }

    required_permissions: list[str] = []
    required_dependencies: list[str] = []

    def __init__(
        self,
        green_claim_service: GreenClaimService = None,
        tool_config: ToolConfig | None = None,
        **deps,
    ):
        if tool_config is None:
            tool_config = ToolConfig(timeout=360)

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🛠️ TOOL][🌿 GREEN_CLAIM] init",
            extra={
                "tool_key": self.key,
                "tool_name": self.name,
                "version": self.version,
                "has_service": green_claim_service is not None,
                "timeout": getattr(tool_config, "timeout", None),
            },
        )

        super().__init__(tool_config=tool_config, **deps)

        if not green_claim_service:
            AGENTVERSE_LOGGER.error(
                LogDomain.TOOL_BAY,
                "[💥 TOOL][🌿 GREEN_CLAIM] missing_dependency",
                extra={
                    "tool_key": self.key,
                    "dependency": "green_claim_service",
                },
            )
            raise ToolExecutionError(
                "green_claim_service must be provided via dependency injection"
            )

        self._service = green_claim_service

    async def _run(
        self,
        text: str | None = None,
        context: dict | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """
        Execute green claim analysis on the provided text.
        """
        ctx_dict = (context or {}) if isinstance(context, dict) else {}
        flags = ctx_dict.get("flags", {}).get("user", {})

        effective_text = flags.get("text") or text or ""

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🛠️ TOOL][🌿 GREEN_CLAIM] execution_start",
            extra={
                "tool_key": self.key,
                "has_text_arg": bool(text),
                "has_context": bool(context),
                "flag_keys": list(flags.keys()) if isinstance(flags, dict) else [],
                "effective_text_len": len(effective_text or ""),
                "kwargs_keys": list(kwargs.keys()),
            },
        )

        if not effective_text.strip():
            AGENTVERSE_LOGGER.warning(
                LogDomain.TOOL_BAY,
                "[⚠️ TOOL][🌿 GREEN_CLAIM] validation_failed_empty_text",
                extra={
                    "tool_key": self.key,
                },
            )
            return self._final_error(
                message="Text parameter is required and cannot be empty",
            )

        try:
            AGENTVERSE_LOGGER.info(
                LogDomain.TOOL_BAY,
                "[🔬 TOOL][🌿 GREEN_CLAIM] analysis_requested",
                extra={
                    "tool_key": self.key,
                    "text_length": len(effective_text),
                    "use_second_opinion": False,
                    "include_formatted_report": True,
                },
            )

            green_claim_analysis = await self._service.run_green_claim_analysis(
                text=effective_text,
                user_id="unknown - agentverse_tool",
                use_second_opinion=False,
                include_formatted_report=True,
            )

            data = green_claim_analysis.model_dump()
            data["widget_type"] = "green_claim"

            AGENTVERSE_LOGGER.info(
                LogDomain.TOOL_BAY,
                "[✅ TOOL][🌿 GREEN_CLAIM] execution_success",
                extra={
                    "tool_key": self.key,
                    "text_length": len(effective_text),
                    "report_length": len(
                        getattr(green_claim_analysis, "formatted_report", "") or ""
                    ),
                    "data_keys": list(data.keys()),
                },
            )

            return {
                "success": True,
                "result": {
                    "answer": green_claim_analysis.formatted_report,
                    "data": data,
                },
            }

        except Exception as e:
            AGENTVERSE_LOGGER.error(
                LogDomain.TOOL_BAY,
                "[💥 TOOL][🌿 GREEN_CLAIM] execution_failed",
                extra={
                    "tool_key": self.key,
                    "text_length": len(effective_text or ""),
                    "error_type": type(e).__name__,
                    "error": str(e),
                },
                exc_info=True,
            )
            # Re-raise so BaseTool.execute() sanitizes the underlying error.
            raise
