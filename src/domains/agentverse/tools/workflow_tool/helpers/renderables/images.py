from typing import Any


def collect_images(
    obj: Any,
    out: list[dict] | None = None,
    *,
    path: tuple[str, ...] = (),
) -> None:
    """Walk ``obj`` appending every image it finds to ``out``.

    Each entry records the ``path`` it was found at, so a caller holding a
    declared output ``path`` can tell which collected images that output refers
    to by prefix rather than by comparing (routinely base64) source strings.
    """
    if out is None or obj is None:
        return

    # Never render provider input images.
    # Example: image_to_prompt.created.request.image
    if "request" in path:
        return

    def is_valid_image_src(value: str) -> bool:
        return value.startswith(("http://", "https://", "data:image/"))

    def append_src(value: Any, *, mime_type: str | None = None, at: tuple[str, ...] = path) -> None:
        if not isinstance(value, str):
            return

        value = value.strip()
        if not value:
            return

        if is_valid_image_src(value):
            out.append(
                {
                    "kind": "image",
                    "src": value,
                    "url": value,
                    "mime_type": mime_type,
                    "storage": "inline_base64" if value.startswith("data:image/") else "url",
                    "path": at,
                }
            )
            return

        # Raw base64 fallback, only for obvious image base64.
        if len(value) > 128 and value.startswith(("/9j/", "iVBORw0", "UklGR", "R0lGOD")):
            mime = mime_type or "image/jpeg"

            if value.startswith("iVBORw0"):
                mime = "image/png"
            elif value.startswith("UklGR"):
                mime = "image/webp"
            elif value.startswith("R0lGOD"):
                mime = "image/gif"

            src = f"data:{mime};base64,{value}"
            out.append(
                {
                    "kind": "image",
                    "src": src,
                    "url": src,
                    "mime_type": mime,
                    "storage": "inline_base64",
                    "path": at,
                }
            )

    if isinstance(obj, str):
        append_src(obj)
        return

    if isinstance(obj, list):
        for i, item in enumerate(obj):
            collect_images(item, out, path=path + (str(i),))
        return

    if not isinstance(obj, dict):
        return

    mime_type = obj.get("mime_type") or obj.get("mime") or obj.get("content_type") or "image/jpeg"

    # Preferred normalized image shapes.
    for key in ("src", "url", "image_url", "data_url"):
        append_src(obj.get(key), mime_type=mime_type, at=path + (key,))

    # Raw base64 shapes.
    for key in ("base64", "b64", "image_base64", "first_image_base64"):
        append_src(obj.get(key), mime_type=mime_type, at=path + (key,))

    # Common arrays.
    for key in ("images", "generated", "image_urls", "urls", "items"):
        value = obj.get(key)
        if isinstance(value, list):
            for i, item in enumerate(value):
                collect_images(item, out, path=path + (key, str(i)))

    # Important known envelopes.
    for key in (
        "workflow_response",
        "env",
        "result",
        "raw_payload",
        "addons",
        "data",
        "raw",
        "value",
        "response",
        "payload",
        "outputs",
    ):
        value = obj.get(key)
        if isinstance(value, dict | list):
            collect_images(value, out, path=path + (key,))

    # Fallback recursion.
    for key, value in obj.items():
        if isinstance(value, dict | list):
            collect_images(value, out, path=path + (str(key),))


def dedupe_images(images: list[dict]) -> list[dict[str, Any]]:
    """Collapse ``collect_images`` output to one entry per source.

    The surviving entry keeps the ``path`` of the first occurrence, which is the
    one a declared output's ``path`` is matched against.
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    for im in images:
        if not isinstance(im, dict):
            continue

        src = im.get("src") or im.get("url")
        if not isinstance(src, str) or not src.strip():
            continue

        src = src.strip()
        if src in seen:
            continue

        seen.add(src)
        out.append(
            {
                "src": src,
                "url": src,
                "mime_type": im.get("mime_type"),
                "storage": im.get("storage"),
                "path": im.get("path") or (),
            }
        )

    return out
