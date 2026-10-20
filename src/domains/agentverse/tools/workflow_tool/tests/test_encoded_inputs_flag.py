"""A percent-encoded ``--inputs=`` blob must reach ``run.inputs`` decoded.

A client may URL-encode the JSON blob (`--inputs=%7B%22a%22%3A1%7D`) so the CLI
splitter's ` --flag` boundary detection can't trip on interior whitespace. The
generic ``coerce_val`` deliberately does *not* percent-decode - it is the
repo-wide coercer for every tool's flags. Decoding happens once, at the
workflow tool's own boundary (``normalize_inputs_payload``), which the ``_run``
resolution ladder reaches via ``extract_flag_from_text`` on the raw text.

These tests pin that seam end to end so the ladder can't rot into dead code.
"""

from __future__ import annotations

import pytest

from src.domains.agentverse.tools.base import ToolExecutionError
from src.domains.agentverse.tools.tool_manager_v1.cli_overrides import extract_cli_overrides
from src.domains.agentverse.tools.tool_manager_v1.params import resolve_param_name
from src.domains.agentverse.tools.workflow_tool.helpers.payload.inputs import (
    normalize_inputs_payload,
)
from src.domains.agentverse.tools.workflow_tool.helpers.payload.pause import (
    extract_flag_from_text,
    first_non_empty,
)
from src.domains.agentverse.utils.coercion import coerce_val
from src.domains.agentverse.utils.strip_quotes import strip_quotes

_SPECS = {"op": {"type": "string"}, "inputs": {"type": "object"}}


def _resolve_inputs(text: str) -> dict:
    """Drive the real ``_run`` input ladder over a raw DSL command.

    Mirrors ``build_local_tool_call_kwargs``: parsed CLI flags are handed to
    ``_run`` both as named params and nested under ``kwargs``, with ``raw_text``
    injected alongside.
    """
    _clean, flags = extract_cli_overrides(
        text,
        param_specs=_SPECS,
        flag_re=None,
        resolve_param_name=resolve_param_name,
        strip_quotes=strip_quotes,
        coerce_val=coerce_val,
    )

    inner = {**flags, "raw_text": text}
    kwargs: dict = {"kwargs": inner}

    raw_text = first_non_empty(
        kwargs.get("raw_text"),
        kwargs.get("text"),
        inner.get("raw_text"),
        inner.get("text"),
    )
    raw_inputs = first_non_empty(
        flags.get("inputs"),
        kwargs.get("inputs"),
        inner.get("inputs"),
        extract_flag_from_text(raw_text or "", "inputs"),
    )
    return normalize_inputs_payload(raw_inputs)


class TestEncodedInputsFlag:
    def test_percent_encoded_blob_decodes(self) -> None:
        text = "@workflow --op=start --inputs=%7B%22image_input%22%3A%22f_abc%22%7D"

        assert _resolve_inputs(text) == {"image_input": "f_abc"}

    def test_encoded_whitespace_survives_the_splitter(self) -> None:
        # The reason clients encode at all: a bare space would end the flag.
        text = "@workflow --op=start --inputs=%7B%22tone%22%3A%20%22warm%20and%20dry%22%7D"

        assert _resolve_inputs(text) == {"tone": "warm and dry"}

    def test_plain_json_blob_still_works(self) -> None:
        text = '@workflow --op=start --inputs={"image_input":"f_abc"}'

        assert _resolve_inputs(text) == {"image_input": "f_abc"}

    def test_no_inputs_flag_yields_empty(self) -> None:
        assert _resolve_inputs("@workflow --op=start") == {}

    def test_undecodable_blob_is_loud(self) -> None:
        # Better a named error than a silent `{}` the workflow can't explain.
        with pytest.raises(ToolExecutionError):
            _resolve_inputs("@workflow --op=start --inputs=not-json-at-all")
