from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.domains.browser_session.clients.browser_runner_client import (
    BrowserRunnerClient,
    BrowserRunnerClientError,
)


class BrowserSessionBootstrapError(RuntimeError):
    """
    Raised when an operation against the Browser Runner fails.
    """


@dataclass(slots=True)
class BrowserSessionStartResult:
    runner_session_id: str
    domain: str
    target_url: str
    final_url: str | None
    interactive_url: str | None
    ready: bool


class BrowserSessionBootstrapper:
    """
    Coordinate browser authentication through the remote Browser Runner.

    This component does not launch Playwright, does not persist session
    metadata, and does not transfer browser storage state.

    The Browser Runner owns the live browser state.
    """

    def __init__(
        self,
        *,
        browser_runner_client: BrowserRunnerClient,
    ) -> None:
        self._browser_runner_client = browser_runner_client

    async def start(
        self,
        *,
        target_url: str,
        agentverse_browser_session_id: str | None = None,
        success_url_contains: str | None = None,
        callback_url: str | None = None,
        callback_token: str | None = None,
        auto_capture: bool = False,
    ) -> BrowserSessionStartResult:
        try:
            result = await self._browser_runner_client.start_session(
                target_url=target_url,
                agentverse_browser_session_id=agentverse_browser_session_id,
                success_url_contains=success_url_contains,
                callback_url=callback_url,
                callback_token=callback_token,
                auto_capture=auto_capture,
            )
        except BrowserRunnerClientError as exc:
            raise BrowserSessionBootstrapError(str(exc)) from exc

        runner_session_id = result.get("session_id")

        if not runner_session_id:
            raise BrowserSessionBootstrapError(
                "Browser Runner did not return a session_id."
            )

        return BrowserSessionStartResult(
            runner_session_id=str(runner_session_id),
            domain=str(result.get("domain") or ""),
            target_url=str(result.get("target_url") or target_url),
            final_url=result.get("final_url"),
            interactive_url=result.get("interactive_url"),
            ready=bool(result.get("ready", False)),
        )

    async def get_status(
        self,
        *,
        runner_session_id: str,
    ) -> dict[str, Any]:
        try:
            return await self._browser_runner_client.get_status(
                session_id=runner_session_id,
            )
        except BrowserRunnerClientError as exc:
            raise BrowserSessionBootstrapError(str(exc)) from exc

    async def render_page(
        self,
        *,
        runner_session_id: str,
        url: str,
        wait_until: str = "domcontentloaded",
        wait_ms: int = 2500,
        max_html_bytes: int = 5_000_000,
    ) -> dict[str, Any]:
        try:
            return await self._browser_runner_client.render_page(
                runner_session_id=runner_session_id,
                url=url,
                wait_until=wait_until,
                wait_ms=wait_ms,
                max_html_bytes=max_html_bytes,
            )
        except BrowserRunnerClientError as exc:
            raise BrowserSessionBootstrapError(str(exc)) from exc

    async def delete(
        self,
        *,
        runner_session_id: str,
    ) -> bool:
        try:
            result = await self._browser_runner_client.delete_session(
                session_id=runner_session_id,
            )
        except BrowserRunnerClientError as exc:
            raise BrowserSessionBootstrapError(str(exc)) from exc

        return bool(result.get("deleted", False))