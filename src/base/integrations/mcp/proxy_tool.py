"""Proxy tool wrapper for remote MCP tools."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar

from src.base.integrations.mcp.client import MCPProxyClient
from src.base.integrations.mcp.proxy_contract import (
    EMPTY_CONTEXT_BUNDLE,
    ToolEnv,
    failed_env,
    has_meaningful_context,
    normalize_context_result,
    normalize_format_result,
    normalize_post_spec,
    normalize_run_result,
)
from src.domains.agentverse.agents.utils.helpers import parse_flags
from src.domains.agentverse.tools.base import BaseTool, ToolConfig, ToolResult
from src.domains.agentverse.tools.spec import ToolSpec

if TYPE_CHECKING:
    from src.base.services.file_service import FileService

logger = logging.getLogger(__name__)


def _wire_file(f: Any) -> Any:
    """Prepare a `files` entry for the remote wire.

    An uploaded file (identified by `file_id`) has its SAS `url` re-minted
    downstream, so the stale `url` is dropped here. A workflow-piped file has no
    `file_id` - its `url` (a data URI) is the payload and must be kept.
    """
    if isinstance(f, dict) and f.get("file_id"):
        return {k: v for k, v in f.items() if k != "url"}
    return f


class MCPProxyToolConfig(ToolConfig):
    """Configuration for MCP proxy tools."""

    remote_tool_name: str = ""
    source: str = "mcp_proxy"


class MCPProxyTool(BaseTool):
    """Expose a remote MCP tool as a local BaseTool."""

    name: ClassVar[str] = "mcp_proxy_tool"
    key: ClassVar[str] = "mcp_proxy"
    version: ClassVar[str] = "1.0.0"

    _tool_spec: ToolSpec | None = None
    _mcp_client: MCPProxyClient | None = None
    _file_service: FileService | None = None

    def __init__(
        self,
        config: MCPProxyToolConfig | None = None,
        mcp_client: MCPProxyClient | None = None,
        tool_spec: ToolSpec | None = None,
        file_service: FileService | None = None,
        **deps: Any,
    ) -> None:
        super().__init__(config, **deps)
        self._mcp_client = mcp_client
        self._tool_spec = tool_spec
        self._file_service = file_service

        if tool_spec:
            self._apply_spec(tool_spec)

    def _apply_spec(self, spec: ToolSpec) -> None:
        object.__setattr__(self, "name", spec.name or f"MCP: {self._spec_key(spec)}")
        object.__setattr__(self, "key", self._spec_key(spec))
        object.__setattr__(self, "version", spec.version or "1.0.0")

    @staticmethod
    def _spec_key(spec: ToolSpec) -> str:
        return spec.key or spec.name.lower().replace(" tool", "").replace(" ", "_")

    @property
    def description(self) -> str:
        if not self._tool_spec:
            return "MCP Proxy Tool"

        return self._tool_spec.description or f"Remote MCP tool: {self._tool_spec.name}"

    @property
    def remote_name(self) -> str:
        return self._tool_spec.name if self._tool_spec else ""

    @property
    def remote_key(self) -> str:
        return self._tool_spec.key or "" if self._tool_spec else ""

    @property
    def is_remote(self) -> bool:
        return True

    def _remote_text_args(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        raw_text = kwargs.get("text", "")
        cli_flags = kwargs.get("cli_flags") or {}

        if cli_flags:
            clean_text = raw_text
        else:
            clean_text, cli_flags = parse_flags(raw_text)

        # `mode` is an explicit --mode flag only; no trigger-keyword inference.

        result: dict[str, Any] = {
            "text": cli_flags.get("text") or clean_text,
            "cli_flags": cli_flags,
        }

        files = kwargs.get("files")
        if files:
            result["files"] = [_wire_file(f) for f in files]

        return result

    async def _call_remote(self, method: str, arguments: dict[str, Any]) -> Any:
        if not self._mcp_client:
            raise RuntimeError("MCP client not configured")
        if not self._tool_spec:
            raise RuntimeError("Tool spec not configured")

        response = await self._mcp_client.execute_tool(
            tool_name=self._tool_spec.key,
            method=method,
            arguments=arguments,
        )

        if not response.success:
            raise RuntimeError(response.error or f"Remote tool method '{method}' failed")

        return response.result

    async def _mint_sas_for_files(self, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Attach SAS `url` to each file dict. Logs file_id, never logs the URL."""
        if not self._file_service:
            raise RuntimeError("FileService not configured on MCPProxyTool; cannot mint SAS URLs.")

        result = []
        for f in files:
            file_id = f.get("file_id")
            user_id = f.get("user_id", "")
            if not file_id:
                result.append(f)
                continue

            sas_url = await self._file_service.mint_sas_url(file_id, user_id)
            logger.debug("Minted SAS URL for file_id=%s [url omitted]", file_id)
            result.append({**f, "url": sas_url})

        return result

    async def _run_env(self, **kwargs: Any) -> ToolEnv:
        """Execute the remote MCP tool and return the typed env it produced."""
        try:
            remote_args = self._remote_text_args(kwargs)

            if remote_args.get("files"):
                remote_args = {
                    **remote_args,
                    "files": await self._mint_sas_for_files(remote_args["files"]),
                }

            logger.debug(
                "Running MCP tool '%s' with arg keys=%s",
                self.remote_key,
                sorted(remote_args),
            )

            raw = await self._call_remote("execute", remote_args)
            return normalize_run_result(raw)

        except Exception as exc:
            logger.error(
                "Error running MCP tool '%s': %s",
                self.remote_key,
                exc,
                exc_info=True,
            )
            return self._error_env(answer=str(exc), error=str(exc), code="mcp_run_failed")

    async def execute(
        self,
        text: str = "",
        cli_flags: dict | None = None,
        files: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        """Execute the remote MCP tool as a BaseTool ToolResult."""
        if text:
            kwargs["text"] = text

        if cli_flags:
            kwargs["cli_flags"] = cli_flags

        if files is not None:
            kwargs["files"] = files

        env = await self._run_env(**kwargs)

        # `result` here becomes the step env declared output `path`s resolve
        # against, so it is serialized from the typed env wholesale - including
        # `success`/`error`, without which a remote failure reaches the workflow
        # engine as a reasonless "Tool failed: <step_id>".
        return ToolResult(
            success=env.success,
            error=env.error,
            result=env.as_env(),
            meta={
                "source": "mcp_proxy",
                "remote_tool": self.remote_key,
            },
        )

    async def provide_context(self, **kwargs: Any) -> dict[str, Any]:
        try:
            if "files" not in kwargs:
                kwargs = {**kwargs, "files": (kwargs.get("args") or {}).get("files")}

            remote_args = self._remote_text_args(kwargs)

            if remote_args.get("files"):
                remote_args = {
                    **remote_args,
                    "files": await self._mint_sas_for_files(remote_args["files"]),
                }

            raw = await self._call_remote(
                "provide_context",
                remote_args,
            )
            result = normalize_context_result(raw, tool_name=self.key)

            if has_meaningful_context(result):
                logger.info(
                    "[MCPProxyTool] provide_context accepted for '%s' keys=%s",
                    self.key,
                    sorted(result),
                )
                return result

            logger.warning(
                "[MCPProxyTool] provide_context for '%s' returned empty normalized bundle",
                self.key,
            )

        except Exception as exc:
            logger.warning(
                "[MCPProxyTool] provide_context failed for '%s': %s",
                self.key,
                exc,
            )

        return dict(EMPTY_CONTEXT_BUNDLE)

    async def format_result(self, llm_output: str) -> dict[str, Any]:
        """Format LLM output through the remote MCP tool formatter."""

        try:
            raw = await self._call_remote(
                "format_result",
                {"text": llm_output},
            )
            result = normalize_format_result(raw, fallback_text=llm_output)

            logger.info("[MCPProxyTool] format_result succeeded for '%s'", self.key)
            return result.as_payload()

        except Exception as exc:
            logger.warning(
                "[MCPProxyTool] format_result failed for '%s': %s",
                self.key,
                exc,
                exc_info=True,
            )
            return self._error_env(
                answer=llm_output,
                error=str(exc),
                code="mcp_format_failed",
            ).as_payload()

    def post_spec(self, *, merged_hints: dict[str, Any], final_prompt: str) -> dict[str, Any]:
        """Return metadata-backed post spec from the remote ToolSpec."""

        metadata = self._tool_spec.metadata if self._tool_spec else {}
        return normalize_post_spec((metadata or {}).get("post_spec"))

    def _error_env(self, *, answer: str, error: str, code: str) -> ToolEnv:
        return failed_env(answer=answer, error=error, code=code, tool_key=self.key)


def create_proxy_tool(
    spec: ToolSpec,
    mcp_client: MCPProxyClient,
    file_service: FileService | None = None,
) -> MCPProxyTool:
    """Create a proxy tool instance from a ToolSpec."""

    return MCPProxyTool(
        config=MCPProxyToolConfig(
            remote_tool_name=spec.name,
            source="mcp_proxy",
        ),
        mcp_client=mcp_client,
        tool_spec=spec,
        file_service=file_service,
    )


def create_proxy_tool_class(
    spec: ToolSpec,
    mcp_client: MCPProxyClient,
    file_service: FileService | None = None,
) -> type[MCPProxyTool]:
    """Create a proxy tool class for registry registration."""

    tool_key = MCPProxyTool._spec_key(spec)

    class DynamicMCPProxyTool(MCPProxyTool):
        name = spec.name or f"MCP: {tool_key}"
        key = tool_key
        version = spec.version or "1.0.0"
        parameters = dict(spec.config or {})

        def __init__(
            self,
            config: MCPProxyToolConfig | None = None,
            mcp_client: MCPProxyClient | None = None,
            tool_spec: ToolSpec | None = None,
            file_service: FileService | None = None,
            **deps: Any,
        ) -> None:
            super().__init__(
                config=config,
                mcp_client=mcp_client or self._default_mcp_client,
                tool_spec=tool_spec or self._default_spec,
                file_service=file_service or getattr(self.__class__, "_default_file_service", None),
                **deps,
            )

    DynamicMCPProxyTool.__name__ = f"MCPProxy_{tool_key}"
    DynamicMCPProxyTool._default_mcp_client = mcp_client
    DynamicMCPProxyTool._default_spec = spec
    DynamicMCPProxyTool._default_file_service = file_service

    return DynamicMCPProxyTool
