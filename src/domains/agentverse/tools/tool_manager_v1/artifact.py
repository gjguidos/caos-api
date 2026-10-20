from __future__ import annotations

from typing import Any

_PLACEHOLDER_TEXTS = {"completed", "complete", "ok", "success", "done", "true", "false", "none"}

_DISPLAY_TEXT_KEYS = (
    "display_text",
    "message",
    "answer",
    "text",
    "report_text",
)

_IMAGE_PRODUCER_HINTS = {
    "draw",
    "image",
    "images",
    "image_generation",
    "product_image",
    "visual",
    "render",
    "illustration",
}


def _looks_like_image_producer(step: str, data: dict[str, Any]) -> bool:
    step_l = (step or "").lower()

    if any(hint in step_l for hint in _IMAGE_PRODUCER_HINTS):
        return True

    tool_name = str(
        data.get("tool")
        or data.get("tool_name")
        or data.get("key")
        or data.get("name")
        or data.get("producer")
        or ""
    ).lower()

    if any(hint in tool_name for hint in _IMAGE_PRODUCER_HINTS):
        return True

    raw = data.get("raw")
    if isinstance(raw, dict):
        raw_tool_name = str(
            raw.get("tool") or raw.get("tool_name") or raw.get("key") or raw.get("name") or ""
        ).lower()

        if any(hint in raw_tool_name for hint in _IMAGE_PRODUCER_HINTS):
            return True

    return False


def _as_dict(x: Any) -> dict[str, Any]:
    return x if isinstance(x, dict) else {}


def _is_real_text(s: Any) -> bool:
    if not isinstance(s, str):
        return False
    t = s.strip()
    return bool(t) and t.lower() not in _PLACEHOLDER_TEXTS


def _extract_explicit_display_text(obj: Any) -> str:
    if not isinstance(obj, dict):
        return ""

    for key in _DISPLAY_TEXT_KEYS:
        value = obj.get(key)
        if isinstance(value, str) and _is_real_text(value):
            return value.strip()

    result = obj.get("result")
    if isinstance(result, dict):
        for key in _DISPLAY_TEXT_KEYS:
            value = result.get(key)
            if isinstance(value, str) and _is_real_text(value):
                return value.strip()

    raw = obj.get("raw")
    if isinstance(raw, dict):
        for key in _DISPLAY_TEXT_KEYS:
            value = raw.get(key)
            if isinstance(value, str) and _is_real_text(value):
                return value.strip()

        result = raw.get("result")
        if isinstance(result, dict):
            for key in _DISPLAY_TEXT_KEYS:
                value = result.get(key)
                if isinstance(value, str) and _is_real_text(value):
                    return value.strip()

    return ""


def _extract_workflow_control(tool_results: dict[str, Any], steps: list[str]) -> dict[str, Any]:
    for step in steps or []:
        v = (tool_results or {}).get(step) or {}
        raw = v.get("raw") or {}
        if not isinstance(raw, dict):
            continue

        candidates = [raw]

        result = raw.get("result")
        if isinstance(result, dict):
            candidates.append(result)

            inner = result.get("result")
            if isinstance(inner, dict):
                candidates.append(inner)

        for node in candidates:
            control: dict[str, Any] = {}

            if isinstance(node.get("result"), dict):
                rr = node["result"]
                if "status" in rr or "run_id" in rr or "output_keys" in rr:
                    control["result"] = rr

            pause = node.get("pause") or node.get("paused")
            if isinstance(pause, dict):
                control["pause"] = pause

            if isinstance(node.get("workflow_key"), str):
                control["workflow_key"] = node["workflow_key"]

            if isinstance(node.get("workflow_name"), str):
                control["workflow_name"] = node["workflow_name"]

            if control:
                return control

    return {}


def _merge_artifact_data(
    *,
    tool_data: Any,
    workflow_control: dict[str, Any],
) -> dict[str, Any] | None:
    merged: dict[str, Any] = {}

    if isinstance(tool_data, dict):
        merged.update(tool_data)
    elif tool_data is not None:
        merged["value"] = tool_data

    if isinstance(workflow_control, dict) and workflow_control:
        merged.update(workflow_control)

    return merged or None


def _env_renderables(env: dict[str, Any]) -> list[dict[str, Any]] | None:
    if not isinstance(env, dict):
        return None

    r = env.get("renderables")
    if isinstance(r, list):
        return r

    result = env.get("result")
    if isinstance(result, dict):
        r = result.get("renderables")
        if isinstance(r, list):
            return r

        inner = result.get("result")
        if isinstance(inner, dict):
            r = inner.get("renderables")
            if isinstance(r, list):
                return r

    raw = env.get("raw")
    if isinstance(raw, dict):
        r = raw.get("renderables")
        if isinstance(r, list):
            return r

        result = raw.get("result")
        if isinstance(result, dict):
            r = result.get("renderables")
            if isinstance(r, list):
                return r

            inner = result.get("result")
            if isinstance(inner, dict):
                r = inner.get("renderables")
                if isinstance(r, list):
                    return r

    return None


def _tool_result_renderables(data: dict[str, Any]) -> list[dict[str, Any]] | None:
    """
    Safe bridge for tools, especially nested WorkflowTool.
    Looks for renderables without changing the normal artifact behavior.
    """
    if not isinstance(data, dict):
        return None

    direct = data.get("renderables")
    if isinstance(direct, list):
        return direct

    for key in ("raw", "payload", "message", "response"):
        node = data.get(key)

        if isinstance(node, dict):
            r = _env_renderables(node)
            if isinstance(r, list):
                return r

            msg = node.get("message")
            if isinstance(msg, dict):
                r = _env_renderables(msg)
                if isinstance(r, list):
                    return r

            data_node = node.get("data")
            if isinstance(data_node, dict):
                r = _env_renderables(data_node)
                if isinstance(r, list):
                    return r

    return None


def _extract_step_texts_from_renderables(renderables: Any) -> dict[str, str]:
    if not isinstance(renderables, list):
        return {}

    current_step: str | None = None
    bucket: dict[str, list[str]] = {}

    for item in renderables:
        if not isinstance(item, dict):
            continue

        kind = item.get("kind")

        # ✅ NEW: recurse into json-wrapped payloads (WorkflowTool via envelope)
        if kind == "json":
            val = item.get("value")
            if isinstance(val, dict):
                for candidate in (
                    val.get("renderables"),
                    val.get("items"),
                    (val.get("content") or {}).get("items")
                    if isinstance(val.get("content"), dict)
                    else None,
                    (val.get("result") or {}).get("renderables")
                    if isinstance(val.get("result"), dict)
                    else None,
                ):
                    if isinstance(candidate, list):
                        nested = _extract_step_texts_from_renderables(candidate)
                        for k, v in nested.items():
                            bucket.setdefault(k, []).append(v)
            continue

        if kind == "combined":
            content = item.get("content")

            if isinstance(content, dict):
                nested_source = (
                    content.get("items")
                    or content.get("renderables")
                    or content.get("children")
                    or []
                )
            else:
                nested_source = (
                    item.get("items")
                    or item.get("renderables")
                    or item.get("children")
                    or item.get("content")
                    or item.get("value")
                    or []
                )

            nested = _extract_step_texts_from_renderables(nested_source)
            for k, v in nested.items():
                bucket.setdefault(k, []).append(v)
            continue

        if kind in {"markdown", "text"}:
            text = item.get("text") or item.get("content")
            if not isinstance(text, str):
                continue

            t = text.strip()

            if t.lower().startswith("### "):
                lines = t.splitlines()
                current_step = lines[0][4:].strip().lower()
                bucket.setdefault(current_step, [])

                rest = "\n".join(lines[1:]).strip()
                if rest and _is_real_text(rest):
                    bucket[current_step].append(rest)

                continue

            if current_step and _is_real_text(t):
                bucket[current_step].append(t)

    return {step: "\n\n".join(parts).strip() for step, parts in bucket.items() if parts}


def _extract_sectioned_texts(env: dict[str, Any]) -> dict[str, str]:
    """
    Extract {section_name: text} from renderables like:
      ### write
      <content...>
      ### ideate
      <content...>
    Works for WorkflowTool and any future tool using the same renderable convention.
    """
    r = _env_renderables(env)
    if not isinstance(r, list):
        return {}

    structured = _extract_step_texts_from_renderables(r)
    if not structured:
        # 🔥 FALLBACK: parse monolithic markdown blobs
        full_text = "\n\n".join(
            [
                (item.get("text") or item.get("content") or "").strip()
                for item in r
                if isinstance(item, dict) and item.get("kind") in {"markdown", "text"}
            ]
        ).strip()

        if full_text:
            sections: dict[str, str] = {}
            current_key = None
            buffer: list[str] = []

            for line in full_text.splitlines():
                line_stripped = line.strip()

                if line_stripped.lower().startswith("### "):
                    # flush previous
                    if current_key and buffer:
                        sections[current_key] = "\n".join(buffer).strip()

                    current_key = line_stripped[4:].strip().lower()
                    buffer = []
                else:
                    if current_key:
                        buffer.append(line)

            # flush last
            if current_key and buffer:
                sections[current_key] = "\n".join(buffer).strip()

            return {k: v for k, v in sections.items() if _is_real_text(v)}

    # normalize keys
    out: dict[str, str] = {}
    for k, v in structured.items():
        kk = (k or "").strip().lower()
        vv = (v or "").strip()
        if kk and _is_real_text(vv):
            out[kk] = vv
    return out


def _is_heading_only_text(s: Any) -> bool:
    return isinstance(s, str) and s.strip().startswith("### ") and "\n" not in s.strip()


def _collect_all_tool_texts_in_order(
    steps: list[str],
    tool_results: dict[str, Any],
) -> dict[str, str]:
    out: dict[str, str] = {}

    for step in steps or []:
        data = (tool_results or {}).get(step) or {}
        if not isinstance(data, dict):
            continue

        explicit = _extract_explicit_display_text(data)
        if explicit:
            out[step] = explicit
            continue
        raw_env = _as_dict(data.get("raw"))

        tool_renderables = _tool_result_renderables(data)
        if isinstance(tool_renderables, list):
            raw_env = {
                **raw_env,
                "renderables": tool_renderables,
            }

        # 1. Prefer workflow/renderable section extraction first.
        sectioned = _extract_sectioned_texts(raw_env)
        if sectioned:
            txt = _format_step_texts(sectioned)
            if _is_real_text(txt):
                out[step] = txt
                continue

        # 2. Then direct tool text.
        t = data.get("text")
        if isinstance(t, str) and _is_real_text(t):
            if not _is_heading_only_text(t):
                out[step] = t.strip()
                continue

        # 3. Then raw message, but avoid heading-only workflow placeholders.
        raw_message = raw_env.get("message")
        if isinstance(raw_message, str) and _is_real_text(raw_message):
            if not _is_heading_only_text(raw_message):
                out[step] = raw_message.strip()
                continue

        # 4. Final deep fallback.
        t = _extract_best_text(raw_env)
        if isinstance(t, str) and _is_real_text(t):
            if not _is_heading_only_text(t):
                out[step] = t.strip()

    return out


def _format_step_texts(step_texts: dict[str, str]) -> str:
    """
    Turn {"write": "...", "ideate": "..."} into:

    ### write
    ...

    ### ideate
    ...
    """
    if not isinstance(step_texts, dict) or not step_texts:
        return ""

    parts: list[str] = []
    for k, v in step_texts.items():
        if not isinstance(v, str) or not _is_real_text(v):
            continue
        kk = (k or "").strip()
        if kk:
            parts.append(f"### {kk}\n{v.strip()}")
        else:
            parts.append(v.strip())

    return _join_text_blocks(parts)


def _join_text_blocks(blocks: list[str]) -> str:
    """Join blocks with spacing, skipping empties."""
    cleaned = [b.strip() for b in (blocks or []) if isinstance(b, str) and b.strip()]
    return "\n\n".join(cleaned).strip()


def _format_all_tools_text(
    all_tool_texts: dict[str, str], *, include_tool_headers: bool = False
) -> str:
    """
    Join per-tool text into one big body. Example:

    ## workflowtool
    ### write
    ...
    ### ideate
    ...

    ## chat
    ...
    """
    if not all_tool_texts:
        return ""

    blocks: list[str] = []
    for tool, txt in all_tool_texts.items():
        if not isinstance(txt, str) or not _is_real_text(txt):
            continue
        if include_tool_headers:
            blocks.append(f"## {tool}\n{txt.strip()}")
        else:
            blocks.append(txt.strip())

    return _join_text_blocks(blocks)


def _walk(obj: Any, *, max_depth: int = 6):
    seen = set()
    queue: list[tuple[Any, int]] = [(obj, 0)]
    while queue:
        cur, depth = queue.pop(0)
        if cur is None:
            continue
        cid = id(cur)
        if cid in seen:
            continue
        seen.add(cid)

        yield cur

        if depth >= max_depth:
            continue

        if isinstance(cur, dict):
            for v in cur.values():
                if isinstance(v, dict | list):
                    queue.append((v, depth + 1))
        elif isinstance(cur, list):
            for v in cur:
                if isinstance(v, dict | list):
                    queue.append((v, depth + 1))


def _normalize_images(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    out: list[dict[str, Any]] = []

    for item in value:
        if isinstance(item, str) and item.strip():
            out.append({"url": item.strip()})
            continue

        if isinstance(item, dict):
            src = item.get("url") or item.get("src") or item.get("image_url") or item.get("href")
            if isinstance(src, str) and src.strip():
                out.append({**item, "url": src.strip()})

    return out


def _extract_images_from_renderables(renderables: Any) -> list[dict[str, Any]]:
    if not isinstance(renderables, list):
        return []

    for item in renderables:
        if not isinstance(item, dict):
            continue

        kind = item.get("kind")

        # combined stacks can wrap image-grid
        if kind == "combined":
            nested = _extract_images_from_renderables(item.get("items"))
            if nested:
                return nested

        if kind in {"images", "image-grid"}:
            imgs = (
                item.get("images")
                or item.get("items")
                or item.get("value")
                or item.get("generated")
            )
            normalized = _normalize_images(imgs)
            if normalized:
                return normalized

        if kind == "image":
            src = item.get("src") or item.get("url")
            if isinstance(src, str) and src.strip():
                return [{"url": src.strip()}]

        # WorkflowTool envelope: {"kind":"json","value": {...}} nesting
        if kind == "json":
            val = item.get("value")
            if isinstance(val, dict):
                res = val.get("result")
                if isinstance(res, dict) and isinstance(res.get("renderables"), list):
                    nested = _extract_images_from_renderables(res["renderables"])
                    if nested:
                        return nested
                if isinstance(val.get("renderables"), list):
                    nested = _extract_images_from_renderables(val["renderables"])
                    if nested:
                        return nested

    return []


def _extract_best_images(obj: Any) -> list[dict[str, Any]]:
    for node in _walk(obj, max_depth=6):
        if isinstance(node, dict):
            r = node.get("renderables")
            if isinstance(r, list):
                imgs = _extract_images_from_renderables(r)
                if imgs:
                    return imgs

            for key in ("images", "generated", "urls", "image_urls"):
                imgs = _normalize_images(node.get(key))
                if imgs:
                    return imgs

    return []


def _extract_best_text_candidates(obj: Any) -> list[str]:
    cands: list[str] = []

    for node in _walk(obj, max_depth=6):
        if not isinstance(node, dict):
            continue

        # direct text-like fields
        for k in ("answer", "text", "content", "output", "message"):
            v = node.get(k)
            if isinstance(v, str) and _is_real_text(v):
                cands.append(v.strip())

        # renderables text
        r = node.get("renderables")
        if isinstance(r, list):
            for item in r:
                if not isinstance(item, dict):
                    continue
                kind = item.get("kind")
                if kind in {"text", "markdown"}:
                    t = item.get("text")
                    if isinstance(t, str) and _is_real_text(t):
                        cands.append(t.strip())

                if kind == "json":
                    val = item.get("value")
                    if isinstance(val, dict):
                        for kk in ("answer", "text", "content", "output", "message"):
                            vv = val.get(kk)
                            if isinstance(vv, str) and _is_real_text(vv):
                                cands.append(vv.strip())

                        res = val.get("result")
                        if isinstance(res, dict):
                            for kk in ("answer", "text", "content", "output", "message"):
                                vv = res.get(kk)
                                if isinstance(vv, str) and _is_real_text(vv):
                                    cands.append(vv.strip())

    return cands


def _extract_best_text(obj: Any) -> str:
    cands = _extract_best_text_candidates(obj)
    if not cands:
        return ""
    cands.sort(key=len, reverse=True)
    return cands[0].strip()


# ---------------------------- public API ----------------------------


def build_task_artifact(
    *,
    steps: list[str],
    tool_results: dict[str, Any],
    context: dict[str, Any] | None = None,
    invoker_addons: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = context or {}
    invoker_addons = invoker_addons or {}
    meta = meta or {}

    images: list[Any] = []
    producer_key: str | None = None
    producer_addons: dict[str, Any] = {}

    for step in steps or []:
        v = (tool_results or {}).get(step) or {}

        if not isinstance(v, dict):
            continue

        if not _looks_like_image_producer(step, v):
            continue

        cand = list((v or {}).get("images") or [])

        if not cand and isinstance(v, dict):
            r = _tool_result_renderables(v)
            if isinstance(r, list):
                cand = _extract_images_from_renderables(r)

        if not cand:
            cand = _extract_best_images((v or {}).get("raw"))

        if cand:
            images = cand
            producer_key = step
            producer_addons = (v or {}).get("addons") or {}
            break

    all_reports: dict[str, Any] = {}

    for _, tdata in (tool_results or {}).items():
        addons_t = (tdata or {}).get("addons") or {}
        reports_t = addons_t.get("reports") or {}
        if isinstance(reports_t, dict):
            for r_name, r_val in reports_t.items():
                all_reports.setdefault(r_name, r_val)

    invoker_reports = (invoker_addons or {}).get("reports") or {}
    if isinstance(invoker_reports, dict):
        for r_name, r_val in invoker_reports.items():
            all_reports.setdefault(r_name, r_val)

    all_tool_texts = _collect_all_tool_texts_in_order(steps, tool_results)

    all_text = _format_all_tools_text(all_tool_texts, include_tool_headers=False)
    step_texts = all_tool_texts

    addons: dict[str, Any] = {
        **_as_dict(producer_addons),
        "usage": meta.get("usage") or {},
        "attachments": context.get("attachments") or [],
        "evidence": context.get("evidence") or [],
        "hints": context.get("hints") or {},
    }

    if all_reports:
        addons["reports"] = all_reports

    tool_data: dict[str, Any] | None = None
    for step in steps or []:
        v = (tool_results or {}).get(step) or {}
        raw = v.get("raw") or {}
        if isinstance(raw, dict):
            result = raw.get("result")
            if isinstance(result, dict):
                inner = result.get("result")
                if isinstance(inner, dict) and "data" in inner:
                    tool_data = inner["data"]
                    break
                if "data" in result:
                    tool_data = result["data"]
                    break

    workflow_control = _extract_workflow_control(tool_results, steps)
    artifact_data = _merge_artifact_data(
        tool_data=tool_data,
        workflow_control=workflow_control,
    )

    tool_name = producer_key or (steps[0] if steps else "chat")

    if images and _is_real_text(all_text):
        artifact: dict[str, Any] = {
            "kind": "combined",
            "content": {
                "text": all_text,
                "images": images,
                "step_texts": step_texts,
            },
            "meta": {
                "tool": tool_name,
                "addons": addons,
            },
        }
        if artifact_data is not None:
            artifact["data"] = artifact_data
        return artifact

    if images:
        artifact: dict[str, Any] = {
            "kind": "images",
            "images": images,
            "meta": {
                "tool": tool_name,
                "addons": addons,
            },
        }
        if artifact_data is not None:
            artifact["data"] = artifact_data
        return artifact

    artifact: dict[str, Any] = {
        "kind": "text",
        "content": all_text or "Sorry — I couldn’t produce a reply this time.",
        "meta": {
            "tool": tool_name,
            "addons": addons,
        },
    }
    if artifact_data is not None:
        artifact["data"] = artifact_data

    return artifact
