"""Error and teardown paths for the session_based_ws decorator.

These are the paths that never get exercised by hand: a rejected handshake, an
unexpected crash during setup, and a crash inside the session handler. The whole point
of the two-phase structure is that the third case must not be reported as the first.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import WebSocketDisconnect, WebSocketException, status

from src.base.decorators.middleware import ws_session_based
from src.base.decorators.middleware.ws_session_based import (
    DEFAULT_REJECT_MESSAGE,
    FORBIDDEN_MESSAGE,
    SETUP_FAILURE_MESSAGE,
    WS_INTERNAL_ERROR,
    WS_POLICY_VIOLATION,
    session_based_ws,
)


def make_websocket() -> MagicMock:
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.send_text = AsyncMock()
    ws.close = AsyncMock()
    return ws


@pytest.fixture
def accepts(monkeypatch):
    """Make _authenticate_and_accept succeed and hand back a known session key."""

    async def _ok(websocket, *_args, **_kwargs):
        await websocket.accept()
        return "session-key-123"

    monkeypatch.setattr(ws_session_based, "_authenticate_and_accept", _ok)


@pytest.fixture
def fails(monkeypatch):
    def _install(exc: BaseException):
        async def _raise(*_args, **_kwargs):
            raise exc

        monkeypatch.setattr(ws_session_based, "_authenticate_and_accept", _raise)

    return _install


@pytest.mark.asyncio
class TestHandshakeFailures:
    @pytest.mark.parametrize(
        ("reason", "expected_message"),
        [
            ("Missing token", "Missing token"),
            ("Token has expired", "Token has expired"),
            ("Insufficient roles or scopes", "Insufficient roles or scopes"),
            ("", DEFAULT_REJECT_MESSAGE),
        ],
    )
    async def test_rejection_closes_1008_explaining_what_actually_failed(
        self, fails, reason, expected_message
    ):
        fails(WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason=reason))
        ws = make_websocket()

        @session_based_ws()
        async def handler(websocket):
            raise AssertionError("handler must not run for a rejected handshake")

        await handler(ws)

        ws.send_text.assert_awaited_once_with(expected_message)
        ws.close.assert_awaited_once_with(code=WS_POLICY_VIOLATION)

    async def test_a_denied_permission_is_not_reported_as_a_bad_token(self, fails):
        """The client authenticated fine; telling it the token is invalid is a lie."""
        fails(PermissionError("Missing permissions: ['agent:read']"))
        ws = make_websocket()

        @session_based_ws()
        async def handler(websocket):
            raise AssertionError("handler must not run for a rejected handshake")

        await handler(ws)

        ws.send_text.assert_awaited_once_with(FORBIDDEN_MESSAGE)
        ws.close.assert_awaited_once_with(code=WS_POLICY_VIOLATION)

    async def test_an_unreachable_authz_service_closes_1011_not_1008(self, fails):
        """An authz outage is our problem, not a policy violation by the client."""
        fails(ConnectionError("authz service unreachable"))
        ws = make_websocket()

        @session_based_ws()
        async def handler(websocket):
            raise AssertionError("handler must not run")

        await handler(ws)

        ws.send_text.assert_awaited_once_with(SETUP_FAILURE_MESSAGE)
        ws.close.assert_awaited_once_with(code=WS_INTERNAL_ERROR)

    async def test_unexpected_setup_error_closes_1011_not_1008(self, fails):
        """A bug during setup is not an auth failure and must not be reported as one."""
        fails(AttributeError("container has no attribute 'authz'"))
        ws = make_websocket()

        @session_based_ws()
        async def handler(websocket):
            raise AssertionError("handler must not run")

        await handler(ws)

        ws.close.assert_awaited_once_with(code=WS_INTERNAL_ERROR)
        assert ws.send_text.await_args.args[0] == SETUP_FAILURE_MESSAGE
        assert DEFAULT_REJECT_MESSAGE not in ws.send_text.await_args.args[0]

    async def test_disconnect_during_handshake_is_silent(self, fails):
        fails(WebSocketDisconnect())
        ws = make_websocket()

        @session_based_ws()
        async def handler(websocket):
            raise AssertionError("handler must not run")

        await handler(ws)

        ws.send_text.assert_not_awaited()
        ws.close.assert_not_awaited()

    async def test_failed_courtesy_message_still_closes_the_socket(self, fails):
        """The close frame carries the status code; a failed send must not skip it."""
        fails(PermissionError("nope"))
        ws = make_websocket()
        ws.send_text.side_effect = RuntimeError("socket already gone")

        @session_based_ws()
        async def handler(websocket):
            raise AssertionError("handler must not run")

        await handler(ws)

        ws.close.assert_awaited_once_with(code=WS_POLICY_VIOLATION)


@pytest.mark.asyncio
class TestSessionFailures:
    async def test_handler_crash_closes_1011_and_never_blames_the_token(self, accepts):
        """The bug this decorator's two-phase split exists to prevent."""
        ws = make_websocket()

        @session_based_ws()
        async def handler(websocket):
            raise RuntimeError("redis went away mid-session")

        await handler(ws)

        ws.send_text.assert_not_awaited()
        ws.close.assert_awaited_once_with(code=WS_INTERNAL_ERROR)

    async def test_client_disconnect_mid_session_closes_nothing(self, accepts):
        ws = make_websocket()

        @session_based_ws()
        async def handler(websocket):
            raise WebSocketDisconnect()

        await handler(ws)

        ws.close.assert_not_awaited()

    async def test_on_ready_runs_with_the_resolved_session_key(self, accepts):
        ws = make_websocket()
        seen = {}

        async def on_ready(websocket, session_key):
            seen["session_key"] = session_key

        @session_based_ws(on_ready=on_ready)
        async def handler(websocket):
            return "done"

        assert await handler(ws) == "done"
        assert seen["session_key"] == "session-key-123"

    async def test_handler_receives_extra_args(self, accepts):
        ws = make_websocket()

        @session_based_ws()
        async def handler(websocket, agent_system_name):
            return agent_system_name

        assert await handler(ws, "joshu-a") == "joshu-a"
