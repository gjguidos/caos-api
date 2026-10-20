from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from openai import BadRequestError

from src.domains.agentverse.utils.estimate_tokens import estimate_tokens

from .context_phase import factory_config
from .datatypes import PostResult
from .protocols import AgentLike, ToolLike, ToolManagerLike

logger = logging.getLogger("llm invoker")

_ALLOWED_IMAGE_KEYS = {
    "size",
    "quality",
    "n",
    "background",
    "style",
}


def _derive_image_params(
    *,
    spec_params: dict[str, Any],
    hints: dict[str, Any],
    cfg: dict[str, Any],
    cli_flags: dict[str, Any],
) -> dict[str, Any]:
    out = {k: v for k, v in (spec_params or {}).items() if k in _ALLOWED_IMAGE_KEYS and v is not None}

    out.update(
        {k: v for k, v in (cli_flags or {}).items() if k in _ALLOWED_IMAGE_KEYS and v is not None}
    )

    out.setdefault("size", hints.get("size") or cfg.get("default_size") or "1024x1024")
    out.setdefault("quality", hints.get("quality") or cfg.get("default_quality") or "standard")
    out.setdefault("style", hints.get("style") or cfg.get("default_style"))

    return out


def _safe_megapixels(size: str | None) -> float | None:
    if not size:
        return None

    try:
        w, h = (int(x.strip()) for x in size.lower().split("x", maxsplit=1))
        return (w * h) / 1_000_000.0
    except Exception:
        return None


def _usage_addons(
    *,
    prompt: str,
    started: datetime,
    ended: datetime,
    model: str | None,
    size: str | None,
    quality: str | None,
    provider: str | None,
    images_count: int,
) -> dict[str, Any]:
    return {
        "usage": {
            "latency_ms": int((ended - started).total_seconds() * 1000),
            "model": model,
            "provider": provider,
            "size": size,
            "quality": quality,
            "images": images_count,
            "prompt_tokens_est": estimate_tokens(prompt),
            "megapixels": _safe_megapixels(size),
            "estimated": True,
        }
    }


def _is_data_image_url(value: str) -> bool:
    return value.startswith("data:image/")


def _is_http_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def _looks_like_base64_image(value: str) -> bool:
    value = value.strip()

    if len(value) < 128:
        return False

    return value.startswith(
        (
            "/9j/",  # jpeg
            "iVBORw0",  # png
            "UklGR",  # webp
            "R0lGOD",  # gif
        )
    )


def _guess_mime_type_from_base64(value: str, fallback: str = "image/jpeg") -> str:
    value = value.strip()

    if value.startswith("/9j/"):
        return "image/jpeg"

    if value.startswith("iVBORw0"):
        return "image/png"

    if value.startswith("UklGR"):
        return "image/webp"

    if value.startswith("R0lGOD"):
        return "image/gif"

    return fallback


def _base64_to_data_url(base64_value: str, mime_type: str | None = None) -> str:
    clean = base64_value.strip()
    mime = mime_type or _guess_mime_type_from_base64(clean)
    return f"data:{mime};base64,{clean}"


def _normalize_image_obj(
    image: Any,
    *,
    size: str | None = None,
    style: str | None = None,
    quality: str | None = None,
    provider: str | None = None,
    index: int | None = None,
) -> dict[str, Any] | None:
    if isinstance(image, str):
        value = image.strip()

        if not value:
            return None

        if _is_http_url(value) or _is_data_image_url(value):
            return {
                "url": value,
                "src": value,
                "size": size,
                "style": style,
                "quality": quality,
                "provider": provider,
                "index": index,
                "storage": "url" if _is_http_url(value) else "inline_base64",
            }

        if _looks_like_base64_image(value):
            mime_type = _guess_mime_type_from_base64(value)
            src = _base64_to_data_url(value, mime_type)
            return {
                "url": src,
                "src": src,
                "base64": value,
                "mime_type": mime_type,
                "size": size,
                "style": style,
                "quality": quality,
                "provider": provider,
                "index": index,
                "storage": "inline_base64",
            }

        return None

    if not isinstance(image, dict):
        return None

    mime_type = (
        image.get("mime_type") or image.get("mime") or image.get("content_type") or "image/jpeg"
    )

    src = image.get("src") or image.get("url") or image.get("image_url") or image.get("data_url")

    base64_value = image.get("base64") or image.get("b64") or image.get("image_base64")

    if isinstance(src, str) and src.strip():
        src = src.strip()

        if _is_http_url(src) or _is_data_image_url(src):
            return {
                **image,
                "url": src,
                "src": src,
                "mime_type": image.get("mime_type") or mime_type,
                "size": image.get("size") or size,
                "style": image.get("style") or style,
                "quality": image.get("quality") or quality,
                "provider": image.get("provider") or provider,
                "index": image.get("index", index),
                "storage": image.get("storage")
                or ("url" if _is_http_url(src) else "inline_base64"),
            }

    if isinstance(base64_value, str) and base64_value.strip():
        base64_value = base64_value.strip()
        src = _base64_to_data_url(base64_value, mime_type)

        return {
            **image,
            "url": src,
            "src": src,
            "base64": base64_value,
            "mime_type": mime_type,
            "size": image.get("size") or size,
            "style": image.get("style") or style,
            "quality": image.get("quality") or quality,
            "provider": image.get("provider") or provider,
            "index": image.get("index", index),
            "storage": "inline_base64",
        }

    return None


def _normalize_images(
    raw: Any,
    *,
    size: str | None,
    style: str | None,
    quality: str | None,
    provider: str | None,
) -> list[dict[str, Any]]:
    try:
        from src.domains.agentverse.tools._helpers.image_normalize import coerce_images_to_objs

        raw_images = coerce_images_to_objs(
            raw,
            size=size,
            style=style,
            quality=quality,
        )
    except Exception:
        logger.exception("coerce_images_to_objs failed; falling back to local normalization")
        raw_images = raw

    if isinstance(raw_images, dict):
        candidates = (
            raw_images.get("images") or raw_images.get("data") or raw_images.get("generated") or []
        )
    elif isinstance(raw_images, list):
        candidates = raw_images
    else:
        candidates = []

    normalized: list[dict[str, Any]] = []

    for index, candidate in enumerate(candidates):
        image = _normalize_image_obj(
            candidate,
            size=size,
            style=style,
            quality=quality,
            provider=provider,
            index=index,
        )

        if image:
            normalized.append(image)

    return normalized


async def _exec_image_generate(
    *,
    agent: AgentLike,
    spec: dict[str, Any],
    hints: dict[str, Any],
    cfg: dict[str, Any],
    cli_flags: dict[str, Any],
) -> PostResult:
    params = _derive_image_params(
        spec_params=spec.get("params") or {},
        hints=hints,
        cfg=cfg,
        cli_flags=cli_flags or {},
    )

    prompt = str(spec.get("prompt") or "").strip()
    size = params.get("size") or "1024x1024"
    quality = params.get("quality") or "standard"
    image_backend = getattr(agent, "image", None)
    model = (
        getattr(image_backend, "model_name", None)
        or getattr(image_backend, "model", None)
    )
    style = params.get("style")

    if not prompt:
        return PostResult(error="image_prompt_missing")

    provider = getattr(getattr(agent, "image", None), "provider", None)

    started = datetime.now(UTC)

    
    try:
        raw = await agent.llm_image_generate_async(
            agent=agent,
            prompt=prompt,
            size=size,
            quality=quality,
        )

    except BadRequestError as exc:
        error = getattr(exc, "body", None)

        if not isinstance(error, dict):
            error = {}

        code = error.get("code") or getattr(exc, "code", None)

        if code != "moderation_blocked":
            raise

        request_id = getattr(exc, "request_id", None)

        logger.warning(
            "Image generation rejected: "
            "code=%s request_id=%s",
            code,
            request_id,
        )

        return PostResult(
            error="moderation_blocked",
            addons={
                "image_generation": {
                    "status": "rejected",
                    "code": code,
                    "request_id": request_id,
                    "message": (
                        "The image provider rejected "
                        "the generation request."
                    ),
                },
                "images": [],
            },
        )


    ended = datetime.now(UTC)

    images = _normalize_images(
        raw,
        size=size,
        style=style,
        quality=quality,
        provider=provider,
    )

    addons = {
        **_usage_addons(
            prompt=prompt,
            started=started,
            ended=ended,
            model=model,
            size=size,
            quality=quality,
            provider=provider,
            images_count=len(images),
        ),
        "images": images,
        "first_image_url": images[0].get("url", "") if images else "",
        "first_image_base64": images[0].get("base64", "") if images else "",
        "has_nsfw": [bool(img.get("has_nsfw", False)) for img in images],
    }

    return PostResult(
        images=images,
        addons=addons,
    )


async def post_steps(
    *,
    tools: list[ToolLike],
    agent: AgentLike,
    final_prompt: str,
    hints: dict[str, Any],
    cli_flags: dict[str, Any],
    tool_manager: ToolManagerLike,
) -> dict[str, PostResult]:
    results: dict[str, PostResult] = {}

    for tool in tools:
        key = getattr(tool, "key", None) or tool.__class__.__name__.lower()
        cfg = factory_config(tool_manager, key)

        try:
            spec: dict[str, Any] = {}
            post_spec = getattr(tool, "post_spec", None)

            if callable(post_spec):
                spec = (
                    post_spec(
                        merged_hints=hints,
                        final_prompt=final_prompt,
                    )
                    or {}
                )

            action = spec.get("action")

            if action == "image.generate":
                results[key] = await _exec_image_generate(
                    agent=agent,
                    spec=spec,
                    hints=hints,
                    cfg=cfg,
                    cli_flags=cli_flags,
                )
                continue

            if action:
                logger.info("[%s] unsupported post action=%s; no-op", key, action)

            results[key] = PostResult()

        except Exception:
            logger.exception("post_steps failed for %s", key)
            results[key] = PostResult(error="post_step_failed")

    return results
