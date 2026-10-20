from __future__ import annotations

import ast
import json
from typing import Any

from src.domains.agentverse.tools.tool_manager_v1.sanitize import (
    json_sanitize as _json_sanitize,
)
from src.domains.agentverse.tools.tool_manager_v1.sanitize import (
    safe_model_dump as _safe_model_dump,
)


def _build_renderables_from_result(result: Any) -> list[dict[str, Any]]:
    renderables: list[dict[str, Any]] = []

    if isinstance(result, dict):
        answer = result.get("answer")
        addons = result.get("addons")

        if isinstance(answer, str) and answer.strip():
            renderables.append({"kind": "text", "value": answer})

        if addons is not None:
            renderables.append({"kind": "json", "value": addons})

    if not renderables:
        renderables.append({"kind": "json", "value": result})

    return renderables


def _try_parse_stringified_payload(value: Any) -> Any:
    if not isinstance(value, str):
        return value

    text = value.strip()
    if not text:
        return value

    if not (
        (text.startswith("{") and text.endswith("}"))
        or (text.startswith("[") and text.endswith("]"))
    ):
        return value

    # Try strict JSON first
    try:
        return json.loads(text)
    except Exception:
        pass

    # Fallback for Python repr-style dict strings coming from logs/adapters
    try:
        return ast.literal_eval(text)
    except Exception:
        return value


def _normalize_result_payload(result: Any) -> Any:
    result = _try_parse_stringified_payload(result)

    if isinstance(result, dict):
        # If the payload is nested like {"result": {...}}, unwrap semantic layer
        inner = result.get("result")
        if isinstance(inner, dict):
            return inner

        # Rescue path for already-wrapped execution payload whose answer
        # accidentally contains a stringified dict
        answer = result.get("answer")
        parsed_answer = _try_parse_stringified_payload(answer)
        if isinstance(parsed_answer, dict):
            inner = parsed_answer.get("result")
            if isinstance(inner, dict):
                return inner

    return result


def as_envelope(raw: Any) -> dict[str, Any]:
    raw_d = _json_sanitize(_safe_model_dump(raw))

    # Case 1: execution payload from any lane
    # {"success": ..., "result": ..., "error": ..., "meta": ...}
    if isinstance(raw_d, dict) and "success" in raw_d and "result" in raw_d:
        success = bool(raw_d.get("success", True))
        error = raw_d.get("error")
        meta = raw_d.get("meta")
        raw_result = raw_d.get("result")

        normalized_result = _normalize_result_payload(raw_result)

        # Preserve pre-built renderables. They may live on raw_d directly (e.g. tools
        # that return a standard envelope) OR one level deeper inside raw_d["result"]
        # (e.g. WorkflowTool wrapped by BaseTool.execute into ToolResult).
        existing_renderables = raw_d.get("renderables")
        if not isinstance(existing_renderables, list) or not existing_renderables:
            if isinstance(raw_result, dict):
                existing_renderables = raw_result.get("renderables")

        if isinstance(existing_renderables, list) and existing_renderables:
            renderables = existing_renderables
        else:
            renderables = _build_renderables_from_result(normalized_result)

        # Preserve message similarly: direct on raw_d first, then inside raw_d["result"]
        existing_message = raw_d.get("message")
        if not isinstance(existing_message, str) or not existing_message:
            if isinstance(raw_result, dict):
                existing_message = raw_result.get("message")

        message = (
            existing_message
            if isinstance(existing_message, str) and existing_message
            else ("OK" if not error else "ERROR")
        )

        return {
            "success": success,
            "message": message,
            "renderables": renderables,
            "result": normalized_result,
            "error": error,
            "code": raw_d.get("code"),
            "meta": meta,
        }

    # Case 2: legacy / tool-own payload that already has renderables + result
    # {"result": {"answer": "...", "addons": {...}}}
    # Also handles WorkflowTool response: {"status":..., "message":..., "renderables":[...], "result":{...}}
    if isinstance(raw_d, dict) and isinstance(raw_d.get("result"), dict):
        normalized_result = _normalize_result_payload(raw_d["result"])

        # Preserve pre-built renderables (e.g. WorkflowTool already builds them)
        existing_renderables = raw_d.get("renderables")
        if isinstance(existing_renderables, list) and existing_renderables:
            renderables = existing_renderables
        else:
            renderables = _build_renderables_from_result(normalized_result)

        existing_message = raw_d.get("message")
        message = (
            existing_message if isinstance(existing_message, str) and existing_message else "OK"
        )

        return {
            "success": True,
            "message": message,
            "renderables": renderables,
            "result": normalized_result,
            "error": raw_d.get("error"),
            "code": raw_d.get("code"),
            "meta": raw_d.get("meta"),
        }

    # Case 3: plain string, maybe actually structured
    normalized_raw = _normalize_result_payload(raw_d)
    if isinstance(normalized_raw, dict):
        renderables = _build_renderables_from_result(normalized_raw)
        return {
            "success": True,
            "message": "OK",
            "renderables": renderables,
            "result": normalized_raw,
            "error": None,
            "code": None,
            "meta": None,
        }

    if isinstance(normalized_raw, str):
        return {
            "success": True,
            "message": "OK",
            "renderables": [{"kind": "text", "value": normalized_raw}],
            "result": {"answer": normalized_raw},
            "error": None,
            "code": None,
            "meta": None,
        }

    return {
        "success": True,
        "message": "OK",
        "renderables": [{"kind": "json", "value": normalized_raw}],
        "result": normalized_raw
        if isinstance(normalized_raw, (dict, list))
        else {"value": normalized_raw},
        "error": None,
        "code": None,
        "meta": None,
    }
