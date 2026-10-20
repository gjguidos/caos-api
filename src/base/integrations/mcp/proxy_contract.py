from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

EMPTY_CONTEXT_BUNDLE: dict[str, Any] = {
    "messages": [],
    "system_overrides": [],
    "attachments": [],
    "evidence": [],
    "hints": {},
    "user_overrides": None,
    "post": [],
}

_CONTEXT_KEYS = tuple(EMPTY_CONTEXT_BUNDLE)

UNSPECIFIED_REMOTE_ERROR = "Remote tool reported a failure without an error message"


class EnvImage(BaseModel):
    """One image as it crosses the proxy seam.

    `url` is a data URI built from the remote `ImageOutput`; `mime` and
    `alt_text` are carried alongside it rather than dropped, so the display
    layer reads the real mime instead of defaulting, and a declared output can
    bind an image sub-path.
    """

    url: str
    mime: str | None = None
    alt_text: str | None = None


class ToolEnv(BaseModel):
    """The shape a remote tool's result takes on this side of the proxy.

    Constructed in exactly one place (`_normalize_tool_result`) so the env's
    shape is owned here rather than re-guessed by each consumer. `as_env`
    produces the flat dict declared output `path`s resolve against; `as_payload`
    produces the narrower wrapper `format_result` hands back.
    """

    answer: str
    addons: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] | None = None
    images: list[EnvImage] = Field(default_factory=list)
    success: bool = True
    error: str | None = None

    def as_env(self) -> dict[str, Any]:
        env = self._result()
        if self.images:
            env["images"] = self._images()
        if not self.success:
            env["success"] = False
            env["error"] = self.error
        return env

    def as_payload(self) -> dict[str, Any]:
        """The `format_result` projection.

        Its only consumer (`llm.helpers.format_phase`) reads it straight back
        into a `ToolEnv`, so every `_result()` key round-trips - `data` included,
        since the format phase feeds the same declared output `path`s `as_env`
        does. `success` does not survive the trip: a failure's reason reaches the
        consumer through `addons["error"]`, which `failed_env` sets.
        """
        return {"result": self._result()}

    def _result(self) -> dict[str, Any]:
        result: dict[str, Any] = {"answer": self.answer, "addons": dict(self.addons)}
        if self.data is not None:
            result["data"] = self.data
        return result

    def _images(self) -> list[dict[str, Any]]:
        return [img.model_dump(exclude_none=True) for img in self.images]


def normalize_run_result(raw: Any) -> ToolEnv:
    return _normalize_tool_result(raw, fallback_text=str(raw or ""))


def normalize_format_result(raw: Any, fallback_text: str) -> ToolEnv:
    return _normalize_tool_result(raw, fallback_text=fallback_text)


def _normalize_tool_result(raw: Any, fallback_text: str) -> ToolEnv:
    if not isinstance(raw, dict):
        return ToolEnv(answer=fallback_text)

    # `success` and `error` carry independent defaults upstream, so a failure
    # with no message is a real shape - gate on `success` alone and supply the
    # message, or the result reads as healthy.
    if raw.get("success") is False:
        return failed_env(
            answer=raw.get("answer") or fallback_text,
            error=raw.get("error") or UNSPECIFIED_REMOTE_ERROR,
        )

    payload: dict[str, Any] = raw
    inner = raw.get("result")
    if isinstance(inner, dict):
        payload = inner

    answer = payload.get("answer")
    if not isinstance(answer, str):
        return ToolEnv(answer=fallback_text)

    addons = payload.get("addons")
    data = payload.get("data")

    return ToolEnv(
        answer=answer,
        addons=addons if isinstance(addons, dict) else {},
        data=data if isinstance(data, dict) else None,
        images=_extract_images(payload.get("images")),
    )


def failed_env(
    *,
    answer: str,
    error: str,
    code: str | None = None,
    tool_key: str | None = None,
) -> ToolEnv:
    """A `ToolEnv` marked failed.

    `error` is the reason, not a code: it is what the workflow engine reports as
    the step error. A short `code` classifying the failure goes to `addons`
    alongside it, together with `addons.error`, which the display layer reads.
    """
    addons: dict[str, Any] = {"error": error}
    if code is not None:
        addons["code"] = code
    if tool_key is not None:
        addons["tool_key"] = tool_key

    return ToolEnv(answer=answer, addons=addons, success=False, error=error)


def normalize_context_result(raw: Any, tool_name: str | None = None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return dict(EMPTY_CONTEXT_BUNDLE)

    out = dict(EMPTY_CONTEXT_BUNDLE)

    if system_instruction := raw.get("system_instruction"):
        out["system_overrides"] = [
            {
                "role": "system",
                "content": _wrap_system_instruction(system_instruction, tool_name),
            }
        ]

    images = _extract_images(raw.get("images"))
    user_instruction = raw.get("user_instruction")

    if user_instruction and images:
        out["user_overrides"] = [
            {"type": "input_text", "text": user_instruction},
            *[{"type": "input_image", "image_url": img.url} for img in images],
        ]
    elif user_instruction:
        out["user_overrides"] = user_instruction

    return out


def _extract_images(raw_images: Any) -> list[EnvImage]:
    if not isinstance(raw_images, list):
        return []
    result = []
    for img in raw_images:
        if not isinstance(img, dict):
            continue
        data = img.get("data")
        mime = img.get("mime")
        if isinstance(data, str) and isinstance(mime, str):
            alt_text = img.get("alt_text")
            result.append(
                EnvImage(
                    url=f"data:{mime};base64,{data}",
                    mime=mime,
                    alt_text=alt_text if isinstance(alt_text, str) else None,
                )
            )
    return result


def _wrap_system_instruction(instruction: str, tool_name: str | None) -> str:
    label = tool_name or "External Tool"

    return (
        f"## Task Instructions - Tool {label}\n\n"
        f"You are operating in the context of the tool **{label}**. "
        "The following instructions are specific to this task and take precedence over your default behavior. "
        "Stay true to your persona, but let these instructions guide how you respond:\n\n"
        f"{instruction}"
    )


def normalize_post_spec(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else {}


def has_meaningful_context(raw: dict[str, Any]) -> bool:
    return any(bool(raw.get(key)) for key in _CONTEXT_KEYS)
