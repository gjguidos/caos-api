"""The websocket handshake carries its JWT in the query string, and uvicorn's protocol
logger logs the raw path. Without this filter a full access token - roles, email, oid -
lands in stdout and Splunk on every connect, so pin the redaction here.
"""

import logging

import pytest

from src.base.logging.logging_config import RedactQueryCredentialsFilter

JWT = (
    "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9"
    ".eyJvaWQiOiI0ZjM1NjY3OCIsInJvbGVzIjpbImFnZW50dmVyc2UtYWRtaW4iXX0"
    ".iNWo8fvmb2BlFlI81pTl44PlEdazMpJgttHHluJ"
)


def make_record(msg: str, *args: object) -> logging.LogRecord:
    return logging.LogRecord(
        name="uvicorn.error",
        level=logging.INFO,
        pathname="websockets_impl.py",
        lineno=274,
        msg=msg,
        args=args,
        exc_info=None,
    )


@pytest.fixture
def redact():
    log_filter = RedactQueryCredentialsFilter()

    def _apply(record: logging.LogRecord) -> str:
        assert log_filter.filter(record) is True
        return record.getMessage()

    return _apply


def test_the_uvicorn_websocket_accept_line_no_longer_carries_the_token(redact):
    record = make_record(
        '%s - "WebSocket %s" [accepted]',
        "172.19.0.1:34772",
        f"/ws/api/v1/agent/min_agent?token={JWT}&agent_name=Min%20agent",
    )

    message = redact(record)

    assert JWT not in message
    assert "token=[REDACTED]" in message
    assert "/ws/api/v1/agent/min_agent" in message
    assert "agent_name=Min%20agent" in message


@pytest.mark.parametrize(
    "param",
    ["token", "access_token", "id_token", "refresh_token", "code", "api_key", "client_secret"],
)
def test_every_credential_param_is_redacted(redact, param):
    record = make_record("%s", f"/ws?{param}=secret-value")

    assert redact(record) == f"/ws?{param}=[REDACTED]"


def test_redaction_stops_at_the_parameter_boundary(redact):
    record = make_record("%s", f"/ws?a=1&token={JWT}&b=2")

    assert redact(record) == "/ws?a=1&token=[REDACTED]&b=2"


def test_a_token_in_the_message_itself_is_redacted(redact):
    """Not every logger passes the URL as an arg."""
    record = make_record(f"Connecting to /ws?token={JWT}")

    assert redact(record) == "Connecting to /ws?token=[REDACTED]"


def test_non_credential_params_are_left_alone(redact):
    record = make_record("%s", "/ws/api/v1/agent/min_agent?agent_name=Min%20agent&signature=hi")

    assert redact(record) == "/ws/api/v1/agent/min_agent?agent_name=Min%20agent&signature=hi"


def test_non_string_args_survive_untouched(redact):
    record = make_record('%s - "WebSocket %s" %d', "172.19.0.1", "/ws?token=abc", 1000)

    assert redact(record) == '172.19.0.1 - "WebSocket /ws?token=[REDACTED]" 1000'


def test_a_record_without_args_is_not_broken(redact):
    record = make_record("Websocket session starting.")

    assert redact(record) == "Websocket session starting."
