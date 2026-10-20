from __future__ import annotations

from typing import Any

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)


def _first_text_from_renderables(renderables: Any) -> str | None:
    if not isinstance(renderables, list):
        return None

    for r in renderables:
        if not isinstance(r, dict):
            continue

        if r.get("kind") in {"text", "markdown"}:
            text = r.get("text") or r.get("content")
            if isinstance(text, str) and text.strip():
                return text.strip()

    return None


def _extract_text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()

    if isinstance(value, dict):
        for key in ("text", "content", "message", "answer", "output"):
            v = value.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()

    return None


def _message_from_env(env: dict[str, Any], renderables: list[Any]) -> str | None:
    msg = env.get("message") or env.get("text") or _first_text_from_renderables(renderables)

    if isinstance(msg, str) and msg.strip():
        return msg.strip()

    msg = _extract_text(env.get("answer"))
    if msg:
        return msg

    msg = _extract_text(env.get("answer_raw"))
    if msg:
        return msg

    result = env.get("result")
    if isinstance(result, dict):
        msg = _extract_text(result.get("answer"))
        if msg:
            return msg

        msg = _extract_text(result.get("answer_raw"))
        if msg:
            return msg

    return None


def _image_renderables_from_result(result: Any) -> list[dict[str, Any]]:
    if not isinstance(result, dict):
        return []

    images = result.get("images")
    if not images:
        return []

    source_items = images if isinstance(images, list) else [images]
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for image in source_items:
        src: Any = None
        mime_type = "image/png"

        if isinstance(image, str):
            src = image

        elif isinstance(image, dict):
            src = (
                image.get("src")
                or image.get("url")
                or image.get("image_url")
                or image.get("data_url")
                or image.get("base64")
                or image.get("b64")
            )
            mime_type = (
                image.get("mime_type")
                or image.get("mime")
                or image.get("content_type")
                or mime_type
            )

        if not isinstance(src, str) or not src.strip():
            continue

        src = src.strip()

        if not src.startswith(("http://", "https://", "data:image/")):
            src = f"data:{mime_type};base64,{src}"

        if src in seen:
            continue

        seen.add(src)
        items.append(
            {
                "src": src,
                "url": src,
                "mime_type": mime_type,
                "storage": "inline_base64" if src.startswith("data:image/") else "url",
            }
        )

    if not items:
        return []

    return [{"kind": "image-grid", "items": items}]


def _is_workflow_control_result(result: Any) -> bool:
    return (
        isinstance(result, dict)
        and isinstance(result.get("outputs"), dict)
        and isinstance(result.get("output_keys"), list)
    )


def _presentable_from_run_snapshot(tool_key: str, env: dict[str, Any]) -> dict[str, Any]:
    """Project a nested ``workflow.run`` snapshot into the presentable shape.

    Nested-workflow steps store the child run snapshot as their env. The
    snapshot carries ``status``/``error`` but no ``success``/``ok`` flag, so the
    generic dict path would default ``ok=True`` and surface a FAILED/CANCELLED
    nested run as successful. Derive ok/error/text from the snapshot fields.
    """
    status = str(env.get("status") or "").upper()
    error = env.get("error")
    ok = status not in {"FAILED", "CANCELLED"} and not error

    if not ok and not error:
        error = f"Nested workflow {status.lower() or 'failed'}"

    name = env.get("workflow_name") or env.get("workflow_key") or tool_key
    text = error if not ok else (f"{name}: {status.title()}" if status else name)
    renderables = [{"kind": "markdown", "text": text}] if text else []

    return {
        "ok": ok,
        "tool": tool_key,
        "message": text,
        "text": text,
        "renderables": renderables,
        "result": {
            "run_id": env.get("run_id"),
            "status": status,
            "workflow_key": env.get("workflow_key"),
        },
        "error": error,
    }


def presentable_from_env(tool_key: str, env: Any) -> dict[str, Any]:
    AGENTVERSE_LOGGER.debug(
        LogDomain.TOOL_BAY,
        "[👷 WORKFLOW][🧩 FORMATTER] presentable_from_env_start",
        extra={
            "tool_key": tool_key,
            "env_type": type(env).__name__ if env is not None else None,
            "env_is_none": env is None,
        },
    )

    if env is None:
        return {
            "ok": False,
            "tool": tool_key,
            "message": None,
            "text": None,
            "renderables": [],
            "result": None,
            "error": "no output",
        }

    if isinstance(env, str):
        return {
            "ok": True,
            "tool": tool_key,
            "message": env,
            "text": env,
            "renderables": [{"kind": "text", "text": env}],
            "result": None,
            "error": None,
        }

    if isinstance(env, dict):
        if env.get("kind") == "workflow.run":
            return _presentable_from_run_snapshot(tool_key, env)

        ok = bool(env.get("success", env.get("ok", True)))

        renderables = env.get("renderables")
        if not isinstance(renderables, list):
            renderables = []

        result = env.get("result") if "result" in env else env
        msg = _message_from_env(env, renderables)

        image_renderables = _image_renderables_from_result(result)

        if image_renderables:
            renderables = [*renderables, *image_renderables]

        if msg and not renderables:
            renderables = [{"kind": "markdown", "text": msg}]

        # Important: nested workflow control results are preserved,
        # but raw/env provenance is not re-expanded here.
        if _is_workflow_control_result(result):
            safe_result = {
                "run_id": result.get("run_id"),
                "status": result.get("status"),
                "output_keys": result.get("output_keys", []),
                "outputs": result.get("outputs", {}),
            }
        else:
            safe_result = result

        return {
            "ok": ok,
            "tool": tool_key,
            "message": msg,
            "text": msg,
            "renderables": renderables,
            "result": safe_result,
            "error": env.get("error"),
        }

    s = str(env)

    return {
        "ok": True,
        "tool": tool_key,
        "message": s,
        "text": s,
        "renderables": [{"kind": "text", "text": s}],
        "result": None,
        "error": None,
    }


def _display_images(env: Any) -> list[dict[str, Any]]:
    """Collect and dedupe the images in a tool env, once, in collection order.

    Entries keep the internal ``src``/``storage``/``path`` fields; ``path`` is what
    lets a declared ``image`` output be matched to its positions here. Use
    :func:`_wire_images` to reduce them to the shape that crosses the wire.
    """
    # Imported lazily to avoid a load-time import cycle through helpers/__init__.
    from src.domains.agentverse.tools.workflow_tool.helpers.renderables.images import (
        collect_images,
        dedupe_images,
    )

    raw: list[dict[str, Any]] = []
    collect_images(env, raw)
    return dedupe_images(raw)


def _wire_images(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each image as ``{"url": ..., "mime_type": ...}`` — internal fields dropped."""
    return [{"url": im["url"], "mime_type": im.get("mime_type")} for im in images]


def present_output_display(tool_key: str, env: Any) -> dict[str, Any]:
    """Slim, display-only view of one tool output for the frontend.

    Returns only what the chat bubble and the canvas actually render:
    ``ok``, ``tool``, ``text``, ``images``, ``error``, plus ``declared_outputs``
    naming which of the tool's declared output keys produced what. Raw execution
    fields (``env``, ``raw``, ``result``, ``renderables``, ``context``, ``addons``)
    are intentionally dropped — they are server-log/debug only and must not
    cross the wire.

    Derived from :func:`presentable_from_env` (the single source of truth for
    ok/text/error), projected to the slim shape plus deduped display images.
    """
    # Lazy import: mirrors _display_images, avoiding a load-time cycle through
    # helpers/__init__.
    from src.domains.agentverse.tools.workflow_tool.helpers.declared_outputs import (
        annotate_declared_outputs,
    )

    base = presentable_from_env(tool_key, env)
    images = _display_images(env) if isinstance(env, dict) else []

    # A failed step produced no declared outputs, so annotating it would report
    # every declared key as an unresolved None.
    declared = annotate_declared_outputs(tool_key, env, images) if base["ok"] else {}

    return {
        "ok": base["ok"],
        "tool": base["tool"],
        "text": base["text"],
        "images": _wire_images(images),
        "error": base["error"],
        "declared_outputs": declared,
    }


def artifact_message_from_presented(presented: dict[str, Any]) -> dict[str, Any]:
    text = presented.get("message") or presented.get("text")
    images = []

    for r in presented.get("renderables") or []:
        if not isinstance(r, dict):
            continue

        if r.get("kind") == "image":
            src = r.get("src") or r.get("url")
            if isinstance(src, str):
                images.append({"url": src})

        elif r.get("kind") in {"images", "image-grid"}:
            for it in r.get("items") or r.get("images") or []:
                if isinstance(it, dict) and isinstance(it.get("url"), str):
                    images.append({"url": it["url"]})

    if images:
        return {
            "kind": "combined",
            "text": text.strip() if isinstance(text, str) else "",
            "images": images,
            "addons": {},
        }

    return {
        "kind": "text",
        "text": text or "",
        "addons": {},
    }


def format_workflow_result(data: dict[str, Any]) -> dict[str, Any]:
    status = data.get("status") or "FAILED"
    err = data.get("error")

    msg = data.get("message") or data.get("text") or status

    renderables = data.get("renderables")
    if not isinstance(renderables, list):
        renderables = [{"kind": "text", "text": msg}]

    res = data.get("result")
    if not isinstance(res, dict):
        res = {
            "run_id": data.get("run_id"),
            "status": status,
            "output_keys": [],
        }

    return {
        "success": status != "FAILED" and not err,
        "message": msg,
        "text": msg,
        "renderables": renderables,
        "result": res,
        "workflow_response": {
            "message": msg,
            "text": msg,
            "renderables": renderables,
            "result": res,
            "run_id": data.get("run_id"),
            "status": status,
            "error": err,
        },
        "error": err,
        "meta": {
            "run_id": data.get("run_id"),
            "status": status,
            "paused": bool(data.get("paused")),
        },
    }
