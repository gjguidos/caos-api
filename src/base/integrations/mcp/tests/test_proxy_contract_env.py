"""Tests for the typed env the proxy adapter constructs.

`proxy_contract` is the single place the remote tool env's shape is decided, so
these cover both the normalization of the remote payload and the two dict
projections (`as_env`, `as_payload`) the rest of the API consumes.
"""

from __future__ import annotations

from src.base.integrations.mcp.proxy_contract import (
    UNSPECIFIED_REMOTE_ERROR,
    EnvImage,
    ToolEnv,
    failed_env,
    normalize_context_result,
    normalize_format_result,
    normalize_run_result,
)


class TestNormalizeRunResult:
    def test_flat_payload_is_read_directly(self) -> None:
        env = normalize_run_result({"answer": "hi", "addons": {"n": 1}, "data": {"k": "v"}})

        assert env.answer == "hi"
        assert env.addons == {"n": 1}
        assert env.data == {"k": "v"}
        assert env.success is True

    def test_nested_result_layer_is_peeled(self) -> None:
        env = normalize_run_result({"result": {"answer": "hi"}})

        assert env.answer == "hi"

    def test_non_dict_payload_falls_back_to_its_string_form(self) -> None:
        assert normalize_run_result("plain text").answer == "plain text"

    def test_missing_answer_falls_back(self) -> None:
        assert normalize_run_result({"addons": {}}).answer == "{'addons': {}}"

    def test_non_dict_addons_and_data_are_dropped(self) -> None:
        env = normalize_run_result({"answer": "hi", "addons": "nope", "data": "nope"})

        assert env.addons == {}
        assert env.data is None

    def test_failure_without_a_message_still_fails(self) -> None:
        env = normalize_run_result({"answer": "nope", "success": False})

        assert env.success is False
        assert env.error == UNSPECIFIED_REMOTE_ERROR

    def test_failure_keeps_its_message(self) -> None:
        env = normalize_run_result({"answer": "nope", "success": False, "error": "boom"})

        assert env.error == "boom"
        assert env.addons["error"] == "boom"


class TestNormalizeFormatResult:
    def test_fallback_text_is_used_when_the_payload_is_unusable(self) -> None:
        assert normalize_format_result(None, fallback_text="original").answer == "original"


class TestImages:
    def test_mime_and_alt_text_are_carried(self) -> None:
        env = normalize_run_result(
            {
                "answer": "hi",
                "images": [{"data": "AA==", "mime": "image/png", "alt_text": "a dot"}],
            }
        )

        assert env.images == [
            EnvImage(url="data:image/png;base64,AA==", mime="image/png", alt_text="a dot")
        ]

    def test_entries_missing_data_or_mime_are_skipped(self) -> None:
        env = normalize_run_result(
            {
                "answer": "hi",
                "images": [{"data": "AA=="}, {"mime": "image/png"}, "not-a-dict"],
            }
        )

        assert env.images == []

    def test_context_bundle_still_reads_image_urls(self) -> None:
        bundle = normalize_context_result(
            {
                "user_instruction": "look",
                "images": [{"data": "AA==", "mime": "image/png"}],
            }
        )

        assert bundle["user_overrides"][1] == {
            "type": "input_image",
            "image_url": "data:image/png;base64,AA==",
        }


class TestProjections:
    def test_as_env_is_flat(self) -> None:
        env = ToolEnv(answer="hi", data={"k": "v"}, images=[EnvImage(url="u", mime="image/png")])

        assert env.as_env() == {
            "answer": "hi",
            "addons": {},
            "data": {"k": "v"},
            "images": [{"url": "u", "mime": "image/png"}],
        }

    def test_as_payload_wraps_the_result(self) -> None:
        assert ToolEnv(answer="hi").as_payload() == {"result": {"answer": "hi", "addons": {}}}

    def test_absent_optionals_leave_no_keys(self) -> None:
        env = ToolEnv(answer="hi").as_env()

        assert "data" not in env
        assert "images" not in env
        assert "success" not in env
        assert "error" not in env

    def test_failure_is_marked_in_the_env(self) -> None:
        out = failed_env(answer="nope", error="boom", code="c", tool_key="t").as_env()

        assert out["success"] is False
        assert out["error"] == "boom"

    def test_a_failure_reaches_format_result_through_addons(self) -> None:
        """`as_payload` carries no `success`/`error`; `format_phase` reads addons."""
        out = failed_env(answer="nope", error="boom", code="c", tool_key="t").as_payload()

        assert out == {
            "result": {
                "answer": "nope",
                "addons": {"error": "boom", "code": "c", "tool_key": "t"},
            }
        }

    def test_failed_env_keeps_the_code_out_of_the_reason(self) -> None:
        env = failed_env(answer="nope", error="boom", code="mcp_run_failed", tool_key="t")

        assert env.error == "boom"
        assert env.addons == {"error": "boom", "code": "mcp_run_failed", "tool_key": "t"}
