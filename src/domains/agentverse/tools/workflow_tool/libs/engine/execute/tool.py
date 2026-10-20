from __future__ import annotations

import json
import re
from typing import Any

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.tools.spec import ToolOutputSpec
from src.domains.agentverse.tools.workflow_tool.helpers.declared_outputs import (
    project_outputs,
    resolve_tool_outputs,
    warn_unresolved_outputs,
)
from src.domains.agentverse.tools.workflow_tool.helpers.output_store import (
    binding_patch_key,
    display_patch_key,
    display_wrapper,
)
from src.domains.agentverse.tools.workflow_tool.helpers.templating import (
    MissingTemplateValueError,
    deep_get,
    parse_single_reference,
    render_template,
)
from src.domains.agentverse.tools.workflow_tool.libs.engine.render_ctx import (
    render_ctx as _render_ctx,
)
from src.domains.agentverse.tools.workflow_tool.libs.engine.resolve_tool_key import (
    resolve_tool_key_from_command as _resolve_tool_key_from_command,
)
from src.domains.agentverse.tools.workflow_tool.libs.engine.status import (
    workflow_status as _workflow_status,
)
from src.domains.agentverse.tools.workflow_tool.libs.engine.workflow_context import (
    workflow_context as _workflow_context,
)
from src.domains.agentverse.tools.workflow_tool.models import (
    PauseState,
    StepExecutionResult,
    WorkflowDefinition,
    WorkflowRun,
    WorkflowStep,
)
from src.domains.agentverse.tools.workflow_tool.protocols import ToolRunnerLike

FAILED_STATUS = "EXECUTE TOOL FAILED"


def _failure(
    *,
    step: WorkflowStep,
    error: str,
    status: str = FAILED_STATUS,
    output: dict[str, Any] | None = None,
) -> StepExecutionResult:
    return StepExecutionResult(
        step_id=step.id,
        ok=False,
        status=status,
        error=error,
        output=output,
    )


def _encode_command_value(value: Any) -> str:
    """Encode a resolved binding value for substitution into a tool command.

    Structured bindings (``object`` and ``list`` outputs) are emitted as compact
    JSON so the consuming tool parses them back into real Python values. The
    consuming side is already in place: a tool declares the receiving param as
    ``object``/``array``, the CLI splitter (``extract_cli_overrides``) captures
    the JSON blob as the flag value, and ``coerce_val`` ``json.loads`` it into a
    dict/list. Compact separators avoid interior whitespace that could interfere
    with the splitter's `` --flag`` boundary detection.

    Scalars keep the historical ``str()`` projection: an ``object`` binding is
    never ``str(dict)``-stringified into a command.
    """
    if isinstance(value, dict | list):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def _render_command(*, run: WorkflowRun, step: WorkflowStep) -> str | StepExecutionResult:
    if not step.command:
        return _failure(step=step, status="FAILED", error=f"tool step '{step.id}' missing command")

    try:
        command = render_template(
            step.command,
            _render_ctx(run),
            strict=True,
            encode=_encode_command_value,
        )
    except MissingTemplateValueError as exc:
        return _failure(step=step, error=f"Workflow step '{step.id}' template error: {exc}")

    command = str(command).strip()
    if not command:
        return _failure(step=step, error=f"Workflow step '{step.id}' rendered empty command")

    return command


def _build_tool_kwargs(
    command: str,
    workflow_ctx: dict[str, Any],
    files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "text": command,
        "raw_text": command,
        "request": command,
        "history": [],
        "context": workflow_ctx,
    }
    if files:
        kwargs["files"] = files
    return kwargs


_DATA_URI_RE = re.compile(r"^data:([^;,]+)[;,]")

_MIME_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
}


def _image_mime(image: dict[str, Any]) -> str | None:
    mime = image.get("mime")
    if isinstance(mime, str) and mime:
        return mime

    url = image.get("url")
    if isinstance(url, str):
        match = _DATA_URI_RE.match(url)
        if match:
            return match.group(1)

    return None


def _as_image_dict(value: Any) -> dict[str, Any] | None:
    """Coerce a resolved binding value into a `{url, ...}` image dict, or None.

    Accepts the projected image shape (dict with a non-empty string `url`) and,
    defensively, the raw ImageOutput shape (`{data: b64, mime}`) by folding it
    into a data URI.
    """
    if not isinstance(value, dict):
        return None

    url = value.get("url")
    if isinstance(url, str) and url:
        return value

    data = value.get("data")
    mime = value.get("mime")
    if isinstance(data, str) and data and isinstance(mime, str) and mime:
        return {"url": f"data:{mime};base64,{data}", "mime": mime}

    return None


def _as_uploaded_file_dict(value: Any) -> dict[str, Any] | None:
    """Coerce a resolved binding value into an uploaded-file wire dict, or None.

    Matches the materialized workflow file-input shape: a dict carrying a
    non-empty string `file_id` (SAS `url` is minted downstream by the consuming
    tool). Whitelist projection so run-state extras never leak onto the wire.
    """
    if not isinstance(value, dict):
        return None

    file_id = value.get("file_id")
    if not (isinstance(file_id, str) and file_id):
        return None

    return {
        k: value[k]
        for k in ("file_id", "user_id", "name", "mime", "size")
        if value.get(k) is not None
    }


def _binding_name(key: str, slot: str) -> str:
    """Base file name for a bound image: `<step_id>.<output_key>` when the
    reference points into the outputs namespace, else the slot name."""
    if key.startswith("outputs."):
        return key.removeprefix("outputs.")
    return slot


def _wire_entry(
    candidate: Any,
    *,
    slot: str,
    base_name: str,
    index: int,
    many: bool,
) -> dict[str, Any] | None:
    """One `files` wire dict for a resolved binding candidate, or None when the
    candidate is neither an uploaded file nor image-shaped."""
    # A materialized file input is always a single dict, never list-valued, so
    # the index/many numbering below only ever applies to image candidates.
    uploaded = _as_uploaded_file_dict(candidate)
    if uploaded is not None:
        wire: dict[str, Any] = {"slot": slot, **uploaded}
        if not wire.get("name"):
            wire["name"] = base_name
        return wire

    image = _as_image_dict(candidate)
    if image is None:
        return None

    mime = _image_mime(image)
    ext = _MIME_EXT.get(mime or "", "")
    name = f"{base_name}.{index}{ext}" if many else f"{base_name}{ext}"

    wire = {"slot": slot, "url": image["url"], "name": name}
    if mime:
        wire["mime"] = mime
    return wire


def _shape_failure(*, step: WorkflowStep, slot: str) -> StepExecutionResult:
    return _failure(
        step=step,
        error=(
            f"Workflow step '{step.id}' file binding for slot '{slot}' "
            "did not resolve to an image or uploaded file"
        ),
    )


def _resolve_file_bindings(
    *,
    run: WorkflowRun,
    step: WorkflowStep,
) -> list[dict[str, Any]] | StepExecutionResult:
    """Resolve `step.file_bindings` into the `files` wire dicts.

    A binding resolves to either an upstream image output or a materialized run
    file input (`${inputs.<key>}`, a dict carrying `file_id`). Strict-fail
    channel (unlike advisory text bindings): an unresolvable reference or a
    resolved value of neither shape fails the step. The shape check is on the
    resolved value only - declared output/input types stay advisory.
    """
    if not step.file_bindings:
        return []

    files: list[dict[str, Any]] = []
    ctx = _render_ctx(run)

    for slot, ref in step.file_bindings.items():
        try:
            key = parse_single_reference(ref)
        except MissingTemplateValueError as exc:
            return _failure(
                step=step,
                error=f"Workflow step '{step.id}' file binding '{slot}': {exc}",
            )

        value = deep_get(ctx, key)
        if value is None:
            return _failure(
                step=step,
                error=f"Workflow step '{step.id}' file binding '{slot}': Missing template value: {key}",
            )

        candidates = value if isinstance(value, list) else [value]
        if not candidates:
            return _shape_failure(step=step, slot=slot)

        base_name = _binding_name(key, slot)
        many = len(candidates) > 1
        wires: list[dict[str, Any]] = []
        for i, candidate in enumerate(candidates):
            wire = _wire_entry(candidate, slot=slot, base_name=base_name, index=i, many=many)
            if wire is None:
                return _shape_failure(step=step, slot=slot)
            wires.append(wire)

        files.extend(wires)

    return files


def _is_nested_workflow_pause(tool_key: str, env: Any) -> bool:
    return tool_key == "workflow" and isinstance(env, dict) and _workflow_status(env) == "PAUSED"


def _nested_workflow_pause_result(
    *,
    run: WorkflowRun,
    step: WorkflowStep,
    tool_key: str,
    env: dict[str, Any],
) -> StepExecutionResult:
    # env is the child's run snapshot (see helpers/response.build_run_snapshot).
    child_pause = env.get("paused") or {}

    parent_pause = PauseState(
        pause_id=f"{run.run_id}:{step.id}",
        step_id=step.id,
        prompt=child_pause.get("prompt", "Nested workflow paused."),
        actions=["continue", "cancel"],
        nested_workflow={
            "child_run_id": env.get("run_id"),
            "child_pause_id": child_pause.get("pause_id"),
            "child_step_id": child_pause.get("step_id"),
            "child_workflow_key": env.get("workflow_key"),
        },
    )

    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        "[👷 WORKFLOW][🔧 DAG TOOL] nested workflow paused",
        extra={
            "step_id": step.id,
            "tool_key": tool_key,
            "child_run_id": env.get("run_id"),
            "env_keys": list(env.keys()),
        },
    )

    raw = display_wrapper(tool_key, env)
    return StepExecutionResult(
        step_id=step.id,
        ok=True,
        status="PAUSED",
        pause=parent_pause,
        output=raw,
        state_patch={display_patch_key(step.id): raw},
    )


def _extract_error(env: Any) -> Any:
    return env.get("error") if isinstance(env, dict) else None


def _failed_tool_result(
    *,
    step: WorkflowStep,
    tool_key: str,
    env: Any,
    error: Any,
) -> StepExecutionResult:
    resolved_error = error or f"Tool failed: {step.id}"

    # The display layer derives output.error/output.ok solely from env
    # (see present_output_display -> presentable_from_env). When the error was
    # resolved from the outer payload rather than env itself, fold it in — and
    # mark the env failed — so the stored wrapper doesn't surface a failed step
    # as {ok: true, error: null}.
    if isinstance(env, dict) and not env.get("error"):
        env = {**env, "error": resolved_error, "success": False}

    return _failure(
        step=step,
        error=resolved_error,
        output={"tool": tool_key, "env": env},
    )


def _successful_tool_result(
    *,
    step: WorkflowStep,
    tool_key: str,
    env: Any,
    outputs: list[ToolOutputSpec],
) -> StepExecutionResult:
    # Storage split: the raw {tool, env} wrapper feeds the display layer, while
    # the projected {key: value} map is the binding/templating namespace.
    display = display_wrapper(tool_key, env)
    projected = project_outputs(env, outputs)
    warn_unresolved_outputs(tool_key, env, outputs)

    patch: dict[str, Any] = {
        binding_patch_key(step.id): projected,
        display_patch_key(step.id): display,
    }

    return StepExecutionResult(
        step_id=step.id,
        ok=True,
        status="COMPLETED",
        output=display,
        state_patch=patch,
    )


async def execute_tool(
    *,
    run: WorkflowRun,
    wf: WorkflowDefinition,
    step: WorkflowStep,
    tool_runner: ToolRunnerLike,
    context: dict[str, Any],
) -> StepExecutionResult:
    command = _render_command(run=run, step=step)
    if isinstance(command, StepExecutionResult):
        return command

    tool_key = _resolve_tool_key_from_command(command)
    if not tool_key:
        return _failure(
            step=step, error=f"Workflow step '{step.id}' command must start with @tool_key"
        )

    files = _resolve_file_bindings(run=run, step=step)
    if isinstance(files, StepExecutionResult):
        return files

    workflow_ctx = _workflow_context(
        run=run,
        wf=wf,
        step=step,
        outer_context=context,
        command=command,
    )

    kwargs = _build_tool_kwargs(command, workflow_ctx, files=files)

    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        "[👷 WORKFLOW][🔧 DAG TOOL] invoke",
        extra={
            "run_id": run.run_id,
            "workflow_name": run.workflow_name,
            "step_id": step.id,
            "tool_key": tool_key,
            "command": command,
        },
    )

    ok, env = await tool_runner(tool_key, kwargs)

    if _is_nested_workflow_pause(tool_key, env):
        return _nested_workflow_pause_result(
            run=run,
            step=step,
            tool_key=tool_key,
            env=env,
        )

    env_success = bool(env.get("success", True)) if isinstance(env, dict) else True

    if not bool(ok) or not env_success:
        return _failed_tool_result(
            step=step,
            tool_key=tool_key,
            env=env,
            error=_extract_error(env),
        )

    return _successful_tool_result(
        step=step,
        tool_key=tool_key,
        env=env,
        outputs=resolve_tool_outputs(tool_key),
    )
