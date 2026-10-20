"""An upstream error reason must survive `_handle_http_error`.

The reason was previously only reachable through `HttpClientError.response_data`,
which no caller reads, so an upstream failure surfaced as a canned status
string.
"""

from __future__ import annotations

import json

import pytest

from src.base.infrastructure.http.async_http_client import AsyncHttpClient, HttpClientError


def _raise(body: str, status: int = 500) -> HttpClientError:
    with pytest.raises(HttpClientError) as excinfo:
        AsyncHttpClient()._handle_http_error("POST", "http://x/y", body, status)
    return excinfo.value


class TestUpstreamDetail:
    @pytest.mark.parametrize("key", ["detail", "error", "message"])
    def test_it_is_preferred_over_the_canned_message(self, key: str) -> None:
        error = _raise(json.dumps({key: "Tool server error: bad slot"}))

        assert str(error) == "Tool server error: bad slot"
        assert error.status_code == 500

    def test_the_raw_body_is_still_attached(self) -> None:
        body = json.dumps({"detail": "bad slot"})

        assert _raise(body).response_data == body

    @pytest.mark.parametrize(
        "body",
        ["not json", "[]", '"a string"', json.dumps({"detail": "   "}), json.dumps({"detail": 42})],
    )
    def test_a_body_with_no_usable_reason_keeps_the_canned_message(self, body: str) -> None:
        assert str(_raise(body)) == "Internal server error"

    def test_the_status_mapping_is_intact(self) -> None:
        assert str(_raise("", status=404)) == "Resource not found"
        assert str(_raise("", status=418)) == "HTTP error 418"
