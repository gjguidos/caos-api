import base64
from typing import Any


def coerce_images_to_objs(
    image_result: Any, *, size: str, style: str | None, quality: str | None
) -> list[dict[str, Any]]:
    def _wrap(url: str) -> dict[str, Any]:
        return {"url": url, "size": size, "style": style, "quality": quality}

    out: list[dict[str, Any]] = []
    if isinstance(image_result, (bytes, bytearray)):
        b64 = base64.b64encode(bytes(image_result)).decode("ascii")
        return [_wrap(f"data:image/png;base64,{b64}")]
    if isinstance(image_result, str):
        s = image_result.strip()
        return [_wrap(s)] if s else []
    if isinstance(image_result, list):
        for item in image_result:
            if isinstance(item, str) and item.strip():
                out.append(_wrap(item.strip()))
            elif isinstance(item, (bytes, bytearray)):
                b64 = base64.b64encode(bytes(item)).decode("ascii")
                out.append(_wrap(f"data:image/png;base64,{b64}"))
            elif isinstance(item, dict):
                u = item.get("url") or item.get("image_url") or item.get("contentUrl")
                if u:
                    out.append(_wrap(str(u)))
                elif item.get("b64_json"):
                    out.append(_wrap(f"data:image/png;base64,{item['b64_json']}"))
        return out
    if isinstance(image_result, dict):
        if "url" in image_result:
            return [_wrap(str(image_result["url"]))]
        data = image_result.get("data")
        if isinstance(data, list):
            for d in data:
                if isinstance(d, dict):
                    if d.get("url"):
                        out.append(_wrap(str(d["url"])))
                    elif d.get("b64_json"):
                        out.append(_wrap(f"data:image/png;base64,{d['b64_json']}"))
            if out:
                return out
        if "result" in image_result and isinstance(image_result["result"], dict):
            return coerce_images_to_objs(
                image_result["result"], size=size, style=style, quality=quality
            )
        if "images" in image_result and isinstance(image_result["images"], list):
            return coerce_images_to_objs(
                image_result["images"], size=size, style=style, quality=quality
            )
    return out
