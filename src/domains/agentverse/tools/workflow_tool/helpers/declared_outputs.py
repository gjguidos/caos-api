"""Declared-output resolution, projection, and display annotation.

A tool declares its typed outputs as ``ToolOutputSpec`` entries (key/label/type/
``path`` into the tool's ``env``). Two consumers need them:

- the engine, which projects them into ``state["outputs"][step_id]`` — the
  ``${outputs.<step_id>.<key>}`` templating namespace;
- the display layer, which annotates the step payload so the frontend can tell
  which declared key a value corresponds to.

Both start from the same ``(tool_key, env)`` pair, and the display layer
recomputes rather than reading the engine's projection because it cannot reach
it: the per-step ``workflow.step`` event carries only the ``{tool, env}`` display
wrapper, with no access to ``run.state``.
"""

from __future__ import annotations

from typing import Any

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.tools.spec import ToolOutputSpec
from src.domains.agentverse.tools.workflow_tool.helpers.templating import deep_get

_UNRESOLVED = object()


def resolve_tool_outputs(tool_key: str) -> list[ToolOutputSpec]:
    """Declared outputs for a tool key, or [] when it declares none.

    Strict-by-design: a tool that declares no outputs contributes no bindable
    keys. An unknown key is not an error - ``get_metadata_for_ref`` returns ``{}``
    for it - and the display layer legitimately asks about names that are not
    registry keys at all: the cancel path stores a synthetic ``workflow`` wrapper,
    and ``output_display`` falls back to the step id for non-canonical shapes.

    Outputs are read from registry metadata, the single source both registration
    paths populate and where ``register_tool`` validates and canonicalizes them at
    import time. A spec that fails validation here is therefore a registration
    bug and is left to raise rather than degrading to an empty namespace, which
    would resurface far away as a ``MissingTemplateValueError`` on an
    ``${outputs.<id>.<key>}`` reference.
    """
    # Lazy import: tool spec modules are imported while the registry is being
    # populated, so a module-level import here would be a startup cycle.
    from src.domains.agentverse.registries import tool_registry_instance

    raw = tool_registry_instance.get_metadata_for_ref(tool_key).get("outputs") or []
    return [ToolOutputSpec(**o) for o in raw]


def project_outputs(env: Any, outputs: list[ToolOutputSpec]) -> dict[str, Any]:
    """Project a tool's declared outputs out of its unwrapped ``env``.

    Produces the clean ``{key: value}`` map that becomes the ``${outputs.<id>}``
    templating namespace. An undeclared key is simply absent, so the strict
    templater raises ``MissingTemplateValueError`` on a bad reference.

    Pure: a declared ``path`` that doesn't resolve binds ``None``, itself a valid
    bindable value. Reporting that is the execution path's job (see
    :func:`warn_unresolved_outputs`) so a spec error is logged once per step
    rather than once per re-projection.
    """
    if not isinstance(env, dict):
        return {}

    return {o.key: deep_get(env, o.path) for o in outputs}


def warn_unresolved_outputs(tool_key: str, env: Any, outputs: list[ToolOutputSpec]) -> None:
    """Log the declared outputs whose ``path`` is absent from ``env``.

    A declared output that isn't there at all is an author-time spec error - a
    stale ``path`` against the tool's real env shape - and shouldn't stay silently
    invisible just because ``None`` is bindable. A path that *is* present and
    legitimately holds ``None`` is not that, so this resolves against a sentinel
    instead of reading the projection back: the projection cannot tell the two
    apart, and reporting the second as a spec error is noise.
    """
    if not isinstance(env, dict):
        return

    unresolved = [
        {"key": o.key, "path": o.path}
        for o in outputs
        if deep_get(env, o.path, _UNRESOLVED) is _UNRESOLVED
    ]
    if not unresolved:
        return

    AGENTVERSE_LOGGER.warning(
        LogDomain.TOOL_BAY,
        "[WORKFLOW] declared output paths did not resolve; binding None",
        extra={"tool_key": tool_key, "unresolved": unresolved},
    )


def annotate_declared_outputs(
    tool_key: str,
    env: Any,
    display_images: list[dict[str, Any]],
) -> dict[str, Any]:
    """Name the declared output keys a step produced, for its display payload.

    Lets the frontend join a downstream step's ``${outputs.<step>.<key>}``
    reference to the value the upstream step actually emitted. ``{}`` when the
    tool declares no outputs or ``env`` isn't the canonical dict shape.

    Values are carried inline, with one exception: an ``image`` output carries
    ``image_indexes`` into the ``images`` array the same payload already has,
    because an image "url" here is routinely an inline base64 data URI and
    re-emitting one would duplicate the whole payload. Other types are sent
    as-is - unlike the raw ``result``/``env`` the slim display shape drops, a
    declared output is an author-chosen projection, so its size is bounded by the
    tool's own spec rather than by whatever the provider happened to return.
    """
    if not isinstance(env, dict):
        return {}

    outputs = resolve_tool_outputs(tool_key)
    if not outputs:
        return {}

    projected = project_outputs(env, outputs)
    annotated: dict[str, Any] = {}

    for o in outputs:
        entry: dict[str, Any] = {"label": o.label, "type": o.type}

        if o.type == "image":
            entry["image_indexes"] = _image_indexes(o.path, display_images)
        else:
            entry["value"] = projected[o.key]

        annotated[o.key] = entry

    return annotated


def _image_indexes(path: str, display_images: list[dict[str, Any]]) -> list[int]:
    """Positions in ``display_images`` collected at or under a declared ``path``.

    ``collect_images`` records where it found each image, so the declared path is
    matched structurally against those - no re-walking ``env`` and no comparing
    base64 source strings. An empty list means "declared as an image but not
    present in the payload", which is reportable, not an error.
    """
    parts = tuple(path.split("."))

    return [i for i, im in enumerate(display_images) if tuple(im["path"])[: len(parts)] == parts]
