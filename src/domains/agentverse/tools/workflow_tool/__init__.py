from __future__ import annotations

import logging
from collections.abc import Callable
from inspect import signature
from typing import Any

from src.base.services.file_service import FileService
from src.domains.agentverse.entities.workflow import slugify_workflow_key
from src.domains.agentverse.exceptions import WorkflowNotFoundError
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.registries import tool_registry_instance as tool_registry
from src.domains.agentverse.tools._helpers.normalize_inputs import (
    normalize_inputs as _normalize_inputs,
)
from src.domains.agentverse.tools.base import BaseTool, ToolExecutionError
from src.domains.agentverse.tools.tool_manager_v1.facts import (
    facts_from_context as _facts_from_context,
)
from src.domains.agentverse.tools.workflow_tool import opts
from src.domains.agentverse.tools.workflow_tool.constants import RESERVED_EXECUTION_KEYS
from src.domains.agentverse.tools.workflow_tool.helpers import (
    handle_get as _handle_get,
)
from src.domains.agentverse.tools.workflow_tool.helpers import (
    handle_resume as _handle_resume,
)
from src.domains.agentverse.tools.workflow_tool.helpers import (
    handle_start as _handle_start,
)
from src.domains.agentverse.tools.workflow_tool.helpers import (
    normalize_op as _normalize_op,
)
from src.domains.agentverse.tools.workflow_tool.helpers import (
    prepare_context as _prepare_context,
)
from src.domains.agentverse.tools.workflow_tool.helpers.authorize_key import (
    authorize_workflow_key as _authorize_workflow_key,
)
from src.domains.agentverse.tools.workflow_tool.helpers.file_inputs import (
    materialize_file_inputs as _materialize_file_inputs,
)
from src.domains.agentverse.tools.workflow_tool.helpers.payload.inputs import (
    normalize_inputs_payload as _normalize_inputs_payload,
)
from src.domains.agentverse.tools.workflow_tool.helpers.payload.pause import (
    extract_flag_from_text as _extract_flag_from_text,
)
from src.domains.agentverse.tools.workflow_tool.helpers.payload.pause import (
    first_non_empty as _first_non_empty,
)
from src.domains.agentverse.tools.workflow_tool.helpers.payload.pause import (
    nested_kwargs as _nested_kwargs,
)
from src.domains.agentverse.tools.workflow_tool.helpers.payload.pause import (
    normalize_pause_payload as _normalize_pause_payload,
)
from src.domains.agentverse.tools.workflow_tool.helpers.resolve_store import (
    workflow_store_prefix as _workflow_store_prefix,
)
from src.domains.agentverse.tools.workflow_tool.libs.engine.resolve_tool_key import (
    tool_keys_in_definition as _tool_keys_in_definition,
)
from src.domains.agentverse.tools.workflow_tool.models import ResolvedWorkflow
from src.domains.agentverse.tools.workflow_tool.spec import workflow_tool
from src.domains.agentverse.tools.workflow_tool.store import (
    InMemoryRunStore,
    RedisWorkflowRunStore,
    RunStore,
)

logger = logging.getLogger("agentverse.tools.workflow_tool")


@tool_registry.bind_spec(workflow_tool)
class WorkflowTool(BaseTool):
    """
    WorkflowTool returns the public workflow.run snapshot:

      - kind/status/run_id/workflow_key/workflow_name
      - paused/error
      - steps: runtime status by step id
      - outputs: display-only output by step id
    """

    name = workflow_tool.name
    key = workflow_tool.key or "workflow"
    description = workflow_tool.description
    version = workflow_tool.version or "0.1.0"
    tool_class = workflow_tool.tool_class or "orchestration"
    requires_tool_runner: bool = True

    parameters: dict[str, Any] = {
        "op": {"type": "string", "required": False},
        "workflow_key": {"type": "string", "required": False},
        "inputs": {"type": "object", "required": False},
        "run_id": {"type": "string", "required": False},
        "pause_payload": {"type": "object", "required": False},
        "context": {"type": "object", "required": False},
    }

    def __init__(self, config: opts.WorkflowToolConfig | dict | None = None):
        super().__init__(config=opts._normalize_config(config))
        self._memory_run_store = InMemoryRunStore()

    # ---------------------------------------------------------------------
    # Context / store helpers
    # ---------------------------------------------------------------------

    def _get_store(
        self, *, context: dict[str, Any] | None, cfg: opts.WorkflowToolConfig
    ) -> RunStore:
        if isinstance(context, dict):
            injected = context.get("workflow_run_store")
            if injected is not None:
                return injected

        repo = getattr(self, "cache", None)
        if repo is not None:
            return RedisWorkflowRunStore(
                repo=repo,
                prefix=_workflow_store_prefix(context),
                ttl_seconds=cfg.run_ttl_seconds,
            )

        return self._memory_run_store

    # ---------------------------------------------------------------------
    # Resolution helpers
    # ---------------------------------------------------------------------
    async def _resolve_workflow(
        self,
        *,
        op: str,
        existing_run: Any | None,
        workflow_key: str | None,
        context: dict[str, Any] | None = None,
    ) -> ResolvedWorkflow:
        """Resolve the executable workflow, its display name, and declared inputs.

        Identity is the ``key`` — already canonicalized by the caller — and the
        returned name is a display-only label (``None`` when none is available).
        Declared inputs are only known for catalog-resolved workflows; the
        snapshot path returns ``[]`` (file inputs are materialized at start, so
        resume never needs them). Resolution order:
          1. WS-E snapshot — on resume, rebuild from ``run.definition`` so an
             in-flight edit to the source row cannot corrupt a paused run.
          2. Live Mongo lookup by key (WS-D) — the catalog is the only resolution
             source, and the key is authorized against the agent's attached
             workflows first.

        There is no inline path: a caller-supplied definition would run an
        arbitrary DAG, and the steps of that DAG can reach workflow-scoped tools
        that are deliberately kept off the direct-invocation surface.
        """
        # 1. Snapshot-on-resume.
        if op == "resume" and existing_run is not None:
            snapshot = getattr(existing_run, "definition", None)
            if snapshot:
                return ResolvedWorkflow(
                    definition=opts.WorkflowDefinition.model_validate(snapshot),
                    name=getattr(existing_run, "workflow_name", None),
                    inputs=[],
                )

        declared = _facts_from_context(context).declared_workflow_keys

        if not workflow_key:
            attached = ", ".join(sorted(declared)) or "none"
            raise ToolExecutionError(
                f"workflow_key is required to resolve a workflow. Attached workflows: {attached}."
            )

        # 2. Catalog lookup by key.
        _authorize_workflow_key(workflow_key, declared=declared)

        try:
            return await self._resolve_from_db(workflow_key)
        except WorkflowNotFoundError as exc:
            raise ToolExecutionError(f"Workflow '{workflow_key}' is not in the catalog.") from exc

    def _assert_tools_constructible(
        self,
        *,
        definition: opts.WorkflowDefinition,
        context: dict[str, Any],
        workflow_key: str | None,
    ) -> None:
        """Fail a run up front when the agent is missing tools the DAG needs.

        Advisory, not the gate: this only checks that each step's tool exists on the
        manager. ``evaluate_policy`` still runs per step and can veto for reasons
        this cannot see (dependencies, capabilities, allow/deny lists), so passing
        here does not promise every step will run.

        Worth doing anyway because the catalog row's tool set is derived from the DAG
        at save time, so the one way to be short a tool is a *long-lived agent*: its
        factory set was resolved at build time, and a workflow edited since can reach
        for a tool it was never equipped with. Naming all of them at once beats dying
        at step N with a single blocked step.
        """
        missing = sorted(
            _tool_keys_in_definition(definition) - _facts_from_context(context).runnable_tool_keys
        )
        if not missing:
            return

        AGENTVERSE_LOGGER.warning(
            LogDomain.TOOL_BAY,
            "[👷 WORKFLOW][🛑 PREFLIGHT] required_tools_missing",
            extra={"workflow_key": workflow_key, "missing_tools": missing},
        )
        raise ToolExecutionError(
            f"Workflow '{workflow_key}' needs tools this agent cannot run: "
            f"{', '.join(missing)}. Attach the workflow to the agent (or equip the "
            f"tools) and rebuild the agent."
        )

    async def _resolve_from_db(self, key: str) -> ResolvedWorkflow:
        """Resolve a workflow key from Mongo via the request-bound DI container.

        The catalog is the only resolution source, so there is nothing to fall
        back to and each failure is reported as itself: ``WorkflowNotFoundError``
        when the key has no row, ``ToolExecutionError`` when the catalog is out of
        reach. Collapsing the two would blame a present workflow for a broken
        container and send an admin hunting for a row that is sitting right there.
        """
        request = getattr(self, "request", None)
        try:
            workflow_service = request.app.container.agentverse.workflow_service()
        except AttributeError as exc:
            raise ToolExecutionError(
                f"Workflow '{key}' could not be resolved: no request-bound container."
            ) from exc
        except Exception as exc:
            AGENTVERSE_LOGGER.exception(
                LogDomain.TOOL_BAY,
                "[👷 WORKFLOW][🧭 RESOLVE] container_access_failed",
                extra={"workflow_key": key},
            )
            raise ToolExecutionError(
                f"Workflow '{key}' could not be resolved: the workflow catalog is unavailable."
            ) from exc

        return await workflow_service.resolve_definition(key)

    def _get_file_service(self) -> FileService | None:
        """FileService from the request-bound DI container, or ``None`` outside a
        request context. Unlike workflow resolution, ``None`` is a real answer
        here: a run with no declared file inputs never needs the service."""
        request = getattr(self, "request", None)
        try:
            return request.app.container.agentverse.file_service()
        except AttributeError:
            return None
        except Exception:
            AGENTVERSE_LOGGER.exception(
                LogDomain.TOOL_BAY,
                "[👷 WORKFLOW][📎 FILE INPUTS] file_service_access_failed",
            )
            return None

    # ---------------------------------------------------------------------
    # Run lifecycle helpers
    # ---------------------------------------------------------------------
    def _new_run(
        self,
        *,
        wf: opts.WorkflowDefinition,
        run_id: str,
        inputs: dict[str, Any] | None,
        workflow_key: str | None = None,
        workflow_name: str | None = None,
    ) -> opts.WorkflowRun:
        now = opts.now_utc_iso()

        return opts.WorkflowRun(
            run_id=run_id,
            workflow_name=workflow_name,
            created_at=now,
            updated_at=now,
            status="RUNNING",
            cursor=0,
            inputs=inputs or {},
            # WS-E: snapshot the resolved definition so resume never re-resolves.
            definition=wf.model_dump(),
            state={
                "_workflow": {
                    "key": workflow_key,
                    "name": workflow_name,
                }
            },
        )

    async def _engine_call(
        self,
        *,
        engine: opts.WorkflowEngine,
        store: Any,
        wf: opts.WorkflowDefinition,
        run: opts.WorkflowRun,
        tool_runner: Callable[..., Any],
        context: dict[str, Any],
        on_step_event: Callable[..., Any] | None = None,
    ) -> tuple[opts.WorkflowRun, dict[str, Any]]:
        return await engine.run_until_pause_or_done(
            run=run,
            wf=wf,
            tool_runner=tool_runner,
            store=store,
            context=context,
            on_step_event=on_step_event,
        )

    # ---------------------------------------------------------------------
    # Tool execution
    # ---------------------------------------------------------------------
    async def _run(
        self,
        socket_emitter,
        op: str = "start",
        action: str | None = None,
        workflow_key: str | None = None,
        inputs: dict[str, Any] | None = None,
        run_id: str | None = None,
        pause_payload: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        cfg: opts.WorkflowToolConfig = self.config

        context = _prepare_context(context)
        store = self._get_store(context=context, cfg=cfg)
        op = _normalize_op(op)

        emit_step_event = (
            opts._make_on_event(socket_emitter=socket_emitter) if socket_emitter else None
        )
        emit_run_snapshot = opts._make_run_snapshot_emitter(socket_emitter=socket_emitter)

        if op == "get":
            return await _handle_get(store=store, run_id=run_id)

        existing_run = None
        # Display-only label. Never caller-supplied: it comes from the resolved
        # catalog row, or from the run being resumed, so what the frontend renders
        # always matches the workflow that is actually executing.
        workflow_name: str | None = None

        if op == "resume" and run_id:
            existing_run = await store.get(run_id)

            if existing_run:
                state = getattr(existing_run, "state", None) or {}
                workflow_meta = state.get("_workflow") or {}

                workflow_key = (
                    workflow_key
                    or workflow_meta.get("key")
                    or state.get("workflow_key")
                    or getattr(existing_run, "workflow_key", None)
                )

                workflow_name = (
                    workflow_meta.get("name")
                    or state.get("workflow_name")
                    or getattr(existing_run, "workflow_name", None)
                )

            AGENTVERSE_LOGGER.warning(
                LogDomain.TOOL_BAY,
                f"[👷 WORKFLOW][🔁 RESUME] recovered_workflow_identity run_id:{run_id} workflow_key:{workflow_key} workflow_name:{workflow_name}",
                extra={
                    "run_id": run_id,
                    "workflow_key": workflow_key,
                    "workflow_name": workflow_name,
                    "found_run": existing_run is not None,
                },
            )

        # Canonicalized at the entry point: this single value is what gets
        # authorized, looked up, stored on the run, and echoed back in events, so
        # authorization and lookup can never disagree about what a key means.
        # Slugifying downstream is idempotent, so it stays as cheap belt-and-braces.
        workflow_key = slugify_workflow_key(workflow_key) or None

        wf, resolved_name, declared_inputs = await self._resolve_workflow(
            op=op,
            existing_run=existing_run,
            workflow_key=workflow_key,
            context=context,
        )
        workflow_name = workflow_name or resolved_name

        engine = opts.WorkflowEngine(max_steps_per_call=int(cfg.max_steps_per_call))

        tool_runner = opts._require_tool_runner(context)
        wrapped_runner = opts._wrap_tool_runner(
            tool_runner=tool_runner,
            context=context,
        )

        if op == "start":
            self._assert_tools_constructible(
                definition=wf, context=context, workflow_key=workflow_key
            )

            inner_kwargs = _nested_kwargs(kwargs)

            raw_text = _first_non_empty(
                kwargs.get("raw_text"),
                kwargs.get("text"),
                inner_kwargs.get("raw_text"),
                inner_kwargs.get("text"),
            )

            # Normalise inputs: accept a dict, a JSON/urlencoded string (structured
            # tool_kwargs path), or extract --inputs=<urlencoded-json> from raw DSL text.
            raw_inputs = _first_non_empty(
                inputs,
                kwargs.get("inputs"),
                inner_kwargs.get("inputs"),
                _extract_flag_from_text(raw_text or "", "inputs"),
            )
            inputs = _normalize_inputs_payload(raw_inputs)

            normalized_inputs = _normalize_inputs(
                inputs=inputs,
                kwargs=kwargs,
                reserved=RESERVED_WORKFLOW_INPUT_KEYS,
                ignore_blank_strings=False,
                ignore_none=False,
                strip_strings=False,
            )

            # Declared `file` inputs arrive as opaque file_id strings; validate
            # them against the caller and swap in resolved file dicts before the
            # run is created, so they persist in run.inputs.
            normalized_inputs = await _materialize_file_inputs(
                inputs=normalized_inputs,
                declared=declared_inputs,
                file_service=self._get_file_service(),
                user_id=context.get("user_id"),
            )

            context["workflow_key"] = workflow_key
            context["workflow_name"] = workflow_name

            AGENTVERSE_LOGGER.info(
                LogDomain.TOOL_BAY,
                "[👷 WORKFLOW][🚀 START] inputs_normalized",
                extra={
                    "workflow_name": workflow_name,
                    "workflow_key": workflow_key,
                    "input_keys": list(normalized_inputs.keys()),
                    "kwargs_keys": list(kwargs.keys()),
                },
            )

            return await _handle_start(
                engine_call=self._engine_call,
                new_run=self._new_run,
                store=store,
                engine=engine,
                wf=wf,
                workflow_key=workflow_key,
                workflow_name=workflow_name,
                run_id=run_id,
                inputs=normalized_inputs,
                context=context,
                tool_runner=wrapped_runner,
                emit_step_event=emit_step_event,
            )

        if op == "resume":
            inner_kwargs = _nested_kwargs(kwargs)

            raw_text = _first_non_empty(
                kwargs.get("raw_text"),
                kwargs.get("text"),
                inner_kwargs.get("raw_text"),
                inner_kwargs.get("text"),
            )

            raw_pause_payload = _first_non_empty(
                pause_payload,
                kwargs.get("pause_payload"),
                inner_kwargs.get("pause_payload"),
                _extract_flag_from_text(raw_text or "", "pause_payload"),
            )

            normalized_pause_payload = _normalize_pause_payload(raw_pause_payload)

            action_value = _first_non_empty(
                action,
                kwargs.get("action"),
                inner_kwargs.get("action"),
                normalized_pause_payload.get("action"),
                normalized_pause_payload.get("data", {}).get("action"),
                normalized_pause_payload.get("decision"),
            )

            if action_value:
                normalized_pause_payload.setdefault("action", action_value)

            AGENTVERSE_LOGGER.warning(
                LogDomain.TOOL_BAY,
                "[👷 WORKFLOW][🚀 RESUME PAYLOAD NORMALIZED]",
                extra={
                    "run_id": run_id,
                    "action": action_value,
                    "raw_pause_payload": raw_pause_payload,
                    "normalized_pause_payload": normalized_pause_payload,
                    "outer_kwargs_keys": list(kwargs.keys()),
                    "inner_kwargs_keys": list(inner_kwargs.keys()),
                    "raw_text": raw_text,
                },
            )

            return await _handle_resume(
                engine_call=self._engine_call,
                store=store,
                engine=engine,
                wf=wf,
                run_id=run_id,
                pause_payload=normalized_pause_payload,
                context=context,
                tool_runner=wrapped_runner,
                emit_step_event=emit_step_event,
                emit_run_snapshot=emit_run_snapshot,
            )

        raise ToolExecutionError(f"Unsupported workflow op: {op}")


# The tool's own control flags are exactly its ``_run`` parameters. They arrive
# flattened out of the nested ``kwargs`` bag the manager builds from the parsed
# CLI overrides, so without reserving them `--op=start --inputs=...` lands `op`
# and `inputs` in ``run.inputs`` and they become bindable as `${inputs.op}`.
# Derived rather than transcribed: adding a parameter to ``_run`` cannot
# silently reintroduce the leak.
RESERVED_WORKFLOW_INPUT_KEYS: frozenset[str] = RESERVED_EXECUTION_KEYS | (
    frozenset(signature(WorkflowTool._run).parameters) - {"self"}
)
