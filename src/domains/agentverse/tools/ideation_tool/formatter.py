from __future__ import annotations

import json
import random
import re
from typing import Any, TypedDict

from src.domains.agentverse.utils.estimate_tokens import estimate_tokens

from .constants import GLOBAL_HANDLE_RE, MULTI_SPACE_RE, TRAILING_PUNCT_RE


class FormatterCtx(TypedDict, total=False):
    topic: str
    mode: str
    platforms: list[str]
    frameworks: list[str]
    num_ideas: int
    seed: int | None
    max_tags_per_platform: int | None
    brand: str | None
    extra_tags: list[str]


def _usage_stub(text: str) -> dict[str, Any]:
    out = estimate_tokens(text)
    return {
        "usage": {
            "latency_ms": None,
            "model": None,
            "provider": None,
            "tokens": {"input": 0, "output": out, "total": out},
            "estimated": True,
        }
    }


def _pluralize(s: str) -> str:
    if s.endswith("y") and len(s) > 1 and s[-2] not in "aeiou":
        return s[:-1] + "ies"
    if not s.endswith("s"):
        return s + "s"
    return s


def topic_variants(topic: str) -> list[str]:
    t = (topic or "").strip().lower()
    if not t:
        return []

    alnum = re.sub(r"[^\w\s\-]", "", t)
    space = re.sub(r"[\s\-]+", " ", alnum).strip()
    dashed = re.sub(r"\s+", "-", space)
    nospace = re.sub(r"\s+", "", space)

    base = {t, space, dashed, nospace}
    tokens = [w for w in space.split() if w]
    if tokens:
        base.update(
            {
                " ".join(_pluralize(w) for w in tokens),
                _pluralize(space),
            }
        )

    return sorted({b for b in base if b})


def line_mentions_tool(s: str, *, trigger_pattern) -> bool:
    if not s:
        return False
    if trigger_pattern.search(s):
        return True
    low = s.lower()
    return any(k in low for k in ("@ideate", "ideation tool", "trigger:"))


def bind_topic(s: str, topic: str, variants: list[str]) -> str:
    if not topic:
        return s

    low = s.lower()
    if any(v and v in low for v in (variants or [topic.lower()])):
        return s

    return f"{s} — {topic}"


def _coerce_hashtags(val: Any) -> list[str]:
    if val is None:
        return []

    if isinstance(val, (list, tuple)):
        seq = [str(v).strip() for v in val if str(v).strip()]
    elif isinstance(val, str):
        seq = [p for p in re.split(r"[,\s]+", val.strip()) if p]
    else:
        seq = []

    out: list[str] = []
    seen: set[str] = set()

    for s in seq:
        tag = s if s.startswith("#") else f"#{s}"
        low = tag.lower()
        if low not in seen:
            seen.add(low)
            out.append(tag)

    return out


def _topic_tag(topic: str) -> str:
    t = re.sub(r"[^\w\s-]", "", (topic or "").strip().lower())
    t = re.sub(r"[\s-]+", "", t)
    return f"#{t}" if t else ""


def _merge_hashtags(
    existing: list[str],
    extra_tags: list[str] | None = None,
    *,
    topic: str = "",
    min_total_tags: int = 0,
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def add(tag: str) -> None:
        if not tag:
            return
        normalized = tag if tag.startswith("#") else f"#{tag}"
        low = normalized.lower()
        if low not in seen:
            seen.add(low)
            out.append(normalized)

    for tag in existing:
        add(tag)

    topic_tag = _topic_tag(topic)
    if topic_tag:
        add(topic_tag)

    for tag in extra_tags or []:
        add(tag)

    if min_total_tags > 0:
        return out[: max(len(out), min_total_tags)]

    return out


def _ensure_hashtags(
    line: str,
    *,
    topic: str,
    extra_tags: list[str] | None = None,
    min_total_tags: int = 0,
) -> str:
    if not line:
        return line

    tokens = line.split()
    existing = [t for t in tokens if t.startswith("#")]
    body = " ".join(t for t in tokens if not t.startswith("#")).strip()

    merged = _merge_hashtags(
        existing,
        extra_tags,
        topic=topic,
        min_total_tags=min_total_tags,
    )
    tags = " ".join(merged).strip()

    return f"{body} {tags}".strip() if tags else body


def _trim_hashtags(s: str, limit: int | None) -> str:
    if limit is None:
        return s

    parts = s.split()
    tags: list[str] = []
    other: list[str] = []

    for tok in parts:
        if tok.startswith("#"):
            tags.append(tok)
        else:
            other.append(tok)

    if limit >= 0:
        tags = tags[:limit]

    return " ".join(other + tags).strip()


def _badge(frames: list[str], idx: int) -> str:
    return f"[{frames[idx % len(frames)]}]" if frames else ""


def _normalize_incoming(data: object) -> str:
    if isinstance(data, str):
        return data.strip()

    def _norm_str(x: Any) -> str:
        return x.strip() if isinstance(x, str) else ""

    items: list[dict[str, Any]] = []

    def _from_any(x: Any) -> None:
        if isinstance(x, dict):
            head = (
                _norm_str(x.get("idea"))
                or _norm_str(x.get("headline"))
                or _norm_str(x.get("title"))
                or _norm_str(x.get("hook"))
            )
            cta = _norm_str(x.get("cta"))
            desc = _norm_str(x.get("description"))
            tags = _coerce_hashtags(x.get("hashtags"))

            if head:
                items.append(
                    {
                        "head": head,
                        "cta": cta,
                        "desc": desc,
                        "tags": tags,
                    }
                )

        elif isinstance(x, str):
            s = _norm_str(x)
            if s:
                items.append(
                    {
                        "head": s,
                        "cta": "",
                        "desc": "",
                        "tags": [],
                    }
                )

    if isinstance(data, list):
        for it in data:
            _from_any(it)
    elif isinstance(data, dict):
        if isinstance(data.get("ideas"), list):
            for it in data["ideas"]:
                _from_any(it)

        if isinstance(data.get("clusters"), list):
            for cl in data["clusters"] or []:
                if isinstance(cl, dict):
                    for it in cl.get("ideas") or []:
                        _from_any(it)

    if not items:
        try:
            return json.dumps(data, ensure_ascii=False)
        except Exception:
            return str(data)

    lines: list[str] = []
    for it in items:
        parts = [it["head"]]
        if it["desc"]:
            parts.append(it["desc"])
        if it["cta"]:
            parts.append(f"(CTA: {it['cta']})")
        if it["tags"]:
            parts.append(" ".join(it["tags"]))
        lines.append(" — ".join(parts))

    return "\n".join(lines)


def _cap_for(
    platform: str,
    *,
    override_cap: int | None,
    cfg_caps: dict[str, dict[str, int]],
) -> int:
    if isinstance(override_cap, int) and override_cap >= 0:
        return override_cap
    return int(((cfg_caps.get(platform) or {}).get("max_hashtags")) or 99)


def format_ideation_result(
    data: object,
    *,
    ctx: FormatterCtx,
    cfg_platform_caps: dict[str, dict[str, int]] | None = None,
    trigger_pattern=None,
) -> dict[str, Any]:
    try:
        mode = ctx.get("mode", "cluster") or "cluster"
        platforms = list(ctx.get("platforms") or [])
        frameworks = list(ctx.get("frameworks") or [])
        need = int(ctx.get("num_ideas") or 12)
        topic = (ctx.get("topic") or "").strip()
        brand = ctx.get("brand") or None
        seed = ctx.get("seed")
        extra_tags = _coerce_hashtags(ctx.get("extra_tags"))

        if isinstance(seed, int):
            random.seed(seed)

        cfg_caps = cfg_platform_caps or {}
        override_cap = ctx.get("max_tags_per_platform")

        raw_text = _normalize_incoming(data)
        candidates = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
        topic_vars = topic_variants(topic) if topic else []

        cleaned: list[str] = []
        for ln in candidates:
            ln = re.sub(r"^\s*\d+\s*[\)\.\-:]\s*", "", ln)

            if trigger_pattern is not None and line_mentions_tool(
                ln, trigger_pattern=trigger_pattern
            ):
                continue

            ln = TRAILING_PUNCT_RE.sub("", ln).strip()
            if not ln:
                continue

            if topic:
                ln = bind_topic(ln, topic, topic_vars)

            if extra_tags or topic:
                ln = _ensure_hashtags(
                    ln,
                    topic=topic,
                    extra_tags=extra_tags,
                )

            cleaned.append(ln)

        if len(cleaned) > need:
            cleaned = cleaned[:need]

        rendered: list[str] = []

        if mode == "social":
            active_platforms = platforms or ["x", "instagram", "linkedin"]
            for i, base in enumerate(cleaned):
                platform = active_platforms[i % len(active_platforms)]
                capped = _trim_hashtags(
                    base,
                    _cap_for(
                        platform,
                        override_cap=override_cap,
                        cfg_caps=cfg_caps,
                    ),
                )
                rendered.append(f"{platform}: {capped}")

        elif mode == "tags-only":
            tag_pool = _merge_hashtags([], extra_tags, topic=topic)
            tag_text = " ".join(tag_pool).strip()
            rendered = [tag_text] if tag_text else cleaned

        elif mode == "framework":
            for i, s in enumerate(cleaned):
                tag = _badge(frameworks, i)
                rendered.append(f"{tag} {s}".strip())

        else:
            rendered = cleaned

        if brand:
            brand_low = brand.lower()
            rendered = [s if brand_low in s.lower() else f"{s} — {brand}" for s in rendered]

        numbered = (
            [f"{i}) {s}" for i, s in enumerate(rendered, start=1)] if rendered else [raw_text]
        )
        text = "\n".join(numbered)

        text = GLOBAL_HANDLE_RE.sub(" ", text)
        text = MULTI_SPACE_RE.sub(" ", text).strip()

        return {
            "result": {
                "answer": text,
                "addons": _usage_stub(text),
            },
            "presented_text": text,
            "presented_lines": text.splitlines(),
        }

    except Exception:
        safe = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        text = GLOBAL_HANDLE_RE.sub(" ", (safe or ""))
        text = MULTI_SPACE_RE.sub(" ", text).strip()

        return {
            "result": {
                "answer": text,
                "addons": _usage_stub(text),
            },
            "presented_text": text,
            "presented_lines": text.splitlines(),
        }
