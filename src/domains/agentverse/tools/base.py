from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from src.base.handlers.exception import (
    GENERIC_ERROR_CODE,
    GENERIC_ERROR_MESSAGE,
    DomainException,
)

logger = logging.getLogger(__name__)

# -------------------- Data models --------------------


class ToolResult(BaseModel):
    success: bool = True
    result: Any = None
    error: str | None = None
    meta: dict | None = None


class ToolConfig(BaseModel):
    """
    Runtime settings shared by all tools. Tool-specific configs
    should subclass this and add fields (kept backward compatible).
    """

    enabled: bool = Field(True, description="Turn the tool on/off without code changes")
    timeout: float | None = Field(
        default=180.0,
        description=(
            "Maximum execution time in seconds. "
            "Set to None for tools whose execution lifecycle "
            "is managed externally."
        ),
    )
    retries: int = Field(0, description="How many times to retry on transient errors")
    retry_backoff: float = Field(0.5, description="Base backoff (seconds) for retries")
    extra: dict[str, Any] = Field(default_factory=dict, description="Tool-specific settings")

    # 🔁 Standardized economy knobs (generic, optional)
    economy_enabled: bool = Field(default=False)
    economy_currency: str = Field(default="USD")
    # Generic pricing table in *integer cents*. Tools can decide which keys to use.
    # Example for RAG: {"per_call_cents":1, "per_doc_cents":1, "per_1k_context_tokens_cents":2}
    # Example for Draw: {"per_call_cents":1, "per_image_cents":5, "per_megapixel_cents":2}
    pricing: dict[str, int] = Field(default_factory=dict)


# -------------------- Exceptions --------------------


class ToolError(Exception): ...


class ToolExecutionError(ToolError):
    def __init__(self, message: str, original_error: Exception | None = None):
        super().__init__(message)
        self.original_error = original_error


class ToolDependencyError(ToolError): ...


# -------------------- BaseTool --------------------


class BaseTool:
    name: ClassVar[str] = "base_tool"
    key: ClassVar[str] = "base"
    version: ClassVar[str] = "0.0.0"

    parameters: ClassVar[dict[str, Any]] = {}
    required_permissions: ClassVar[list[str]] = []
    required_dependencies: ClassVar[list[str]] = []
    

    # If bound by ToolManager, these may exist:
    agent: Any = None  # (optional) Agent object that exposes memory methods
    llm: Any = None  # (optional) for _safe_model_meta
    job_policy_type: ClassVar[type[Any] | None] = None

    def __init__(self, config: ToolConfig | None = None, **deps):
        self.config = self._coerce_config(config)
        self.runtime_dependencies: dict[str, Any] = {}

        self.operation_service = None
        self.operation_runner = None
        self._validate_dependencies(deps)
        for k, v in deps.items():
            setattr(self, k, v)

    # -------------------- Config/deps helpers --------------------

    def _coerce_config(self, cfg) -> ToolConfig:
        if cfg is None:
            return ToolConfig()
        if isinstance(cfg, ToolConfig):
            return cfg
        if isinstance(cfg, BaseModel):  # keep subclassed Pydantic config intact
            return cfg  # type: ignore[return-value]
        if isinstance(cfg, SimpleNamespace):
            cfg = vars(cfg)
        if isinstance(cfg, dict):
            # Respect subclass fields if someone passed a dict for a subclass
            try:
                # Attempt constructing our subclass if __class__ type is overriding
                return self.__class__.__dict__.get("ConfigModel", ToolConfig)(**cfg)  # type: ignore
            except Exception:
                known = {
                    k: cfg.get(k)
                    for k in (
                        "enabled",
                        "timeout",
                        "retries",
                        "retry_backoff",
                        "economy_enabled",
                        "economy_currency",
                        "pricing",
                    )
                    if k in cfg
                }
                extra = cfg.get("extra", {})
                other = {k: v for k, v in cfg.items() if k not in known and k != "extra"}
                return ToolConfig(**known, extra={**extra, **other})
        return ToolConfig()

    def _validate_dependencies(self, deps: dict[str, Any]):
        missing = set(self.required_dependencies) - deps.keys()
        if missing:
            raise ToolDependencyError(f"Missing required dependencies: {missing}")

    # -------------------- Usage / economy helpers --------------------

    @staticmethod
    def _latency_ms(start: datetime, end: datetime | None = None) -> int:
        end = end or datetime.now(UTC)
        return int((end - start).total_seconds() * 1000)

    # -------------------- Standard final envelopes --------------------

    @staticmethod
    def _final_error(
        *,
        message: str,
        answer: str = "",
        code: str = "TOOL_ERROR",
        result_overrides: dict[str, Any] | None = None,
        search_metrics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a failure envelope. ``message`` must already be user-safe —
        never pass raw ``str(e)`` here."""
        payload: dict[str, Any] = {
            "answer": answer,
            "sources": [],
            "search_metrics": search_metrics or {},
        }
        if result_overrides:
            payload.update(result_overrides)
        return {
            "success": False,
            "error": message,
            "message": message,
            "code": code,
            "result": payload,
        }

    # -------------------- Execution with timeout/retries --------------------

    async def execute(
        self,
        socket_emitter,
        **kwargs,
    ) -> ToolResult | dict[str, Any]:

        if not self.config.enabled:
            return self._final_error(
                message=f"{self.name} is disabled.",
                code="TOOL_DISABLED",
            )

        timeout = self.config.timeout
        retries = int(self.config.retries)
        backoff = float(self.config.retry_backoff)

        last_message = GENERIC_ERROR_MESSAGE
        last_code = GENERIC_ERROR_CODE

        for attempt in range(retries + 1):
            try:
                run = self._run(
                    socket_emitter=socket_emitter,
                    **kwargs,
                )

                if timeout is None:
                    out = await run
                else:
                    out = await asyncio.wait_for(
                        run,
                        timeout=float(timeout),
                    )

            except TimeoutError:
                last_message = (
                    f"{self.name} timed out. "
                    "Please try again."
                )
                last_code = "TOOL_TIMEOUT"

                logger.warning(
                    "Tool '%s' timed out after %.1fs",
                    self.key,
                    timeout,
                )

            except asyncio.CancelledError:
                # Cancellation is control flow, not a tool failure.
                logger.info(
                    "Tool '%s' execution cancelled",
                    self.key,
                )
                raise

            except DomainException as e:
                logger.warning(
                    "Tool '%s' expected error (%s): %s",
                    self.key,
                    e.error_code,
                    e.message,
                )

                return self._final_error(
                    message=e.message,
                    code=e.error_code,
                )

            except ToolExecutionError as e:
                logger.warning(
                    "Tool '%s' execution error: %s",
                    self.key,
                    e,
                )

                return self._final_error(
                    message=str(e),
                    code="TOOL_ERROR",
                )

            except Exception:
                last_message = GENERIC_ERROR_MESSAGE
                last_code = GENERIC_ERROR_CODE

                logger.exception(
                    "Tool '%s' failed with an unexpected error",
                    self.key,
                )

            else:
                if (
                    isinstance(out, dict)
                    and "success" in out
                    and "result" in out
                ):
                    return out

                return ToolResult(
                    success=True,
                    result=out,
                )

            if attempt < retries:
                await asyncio.sleep(
                    backoff * (attempt + 1)
                )

        return self._final_error(
            message=last_message,
            code=last_code,
        )
    
    def bind_runtime_dependencies(
        self,
        dependencies: dict[str, Any] | None,
    ) -> None:
        """
        Bind application-owned runtime services to this tool instance.

        These dependencies are owned by the AgentVerse runtime/container,
        not by the tool configuration.
        """
        dependencies = dependencies or {}

        self.runtime_dependencies = dict(dependencies)

        self.operation_service = dependencies.get(
            "operation_service"
        )
        self.operation_runner = dependencies.get(
            "operation_runner"
        )

    def build_job_policy(
        self,
        **kwargs: Any,
    ) -> Any | None:
        policy_type = type(self).job_policy_type

        if policy_type is None:
            return None

        return policy_type(**kwargs)

    # -------------------- To be overridden by tools --------------------

    async def _run(self, socket_emitter, **params) -> Any:
        raise NotImplementedError
