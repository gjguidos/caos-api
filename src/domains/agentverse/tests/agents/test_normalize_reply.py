"""Tests for normalize_reply, the reply boundary.

The outer strip lives here rather than in the refine phase: replies render as
markdown, so interior whitespace normalization is invisible at best and
destructive at worst (code indentation, trailing-double-space line breaks).
"""

from __future__ import annotations

from src.domains.agentverse.agents.utils.helpers import normalize_reply


class TestOuterStrip:
    def test_leading_and_trailing_newlines_go(self):
        assert normalize_reply({"kind": "text", "text": "\n\nhello\n\n"})["text"] == "hello"

    def test_leading_and_trailing_spaces_go(self):
        assert normalize_reply({"kind": "text", "text": "  hello  "})["text"] == "hello"

    def test_combined_kind_is_stripped_too(self):
        reply = normalize_reply({"kind": "combined", "text": "\nhi\n", "images": []})

        assert reply["text"] == "hi"

    def test_non_string_text_is_left_alone(self):
        assert normalize_reply({"kind": "images", "images": [], "text": None})["text"] is None


class TestInteriorIsUntouched:
    def test_code_indentation_survives(self):
        text = "```python\ndef foo():\n    if x:\n        return 1\n```"

        assert normalize_reply({"kind": "text", "text": text})["text"] == text

    def test_indented_block_outside_fences_survives(self):
        text = "Example:\n\n    def foo():\n        return 1"

        assert normalize_reply({"kind": "text", "text": text})["text"] == text

    def test_markdown_hard_line_break_survives(self):
        text = "line one  \nline two"

        assert normalize_reply({"kind": "text", "text": text})["text"] == text

    def test_interior_space_runs_survive(self):
        text = "a     b"

        assert normalize_reply({"kind": "text", "text": text})["text"] == text

    def test_blank_line_runs_survive(self):
        text = "a\n\n\n\nb"

        assert normalize_reply({"kind": "text", "text": text})["text"] == text


class TestDefaults:
    def test_non_dict_becomes_empty_text_reply(self):
        assert normalize_reply(None) == {"kind": "text", "text": ""}

    def test_missing_keys_are_defaulted(self):
        reply = normalize_reply({"kind": "text"})

        assert reply["text"] == ""
        assert reply["addons"] == {}
        assert reply["meta"] == {}
