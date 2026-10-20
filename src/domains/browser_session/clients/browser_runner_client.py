from __future__ import annotations

from typing import Any


class BrowserRunnerClientError(RuntimeError):
    """
    Raised when communication with the Browser Runner fails.
    """


class BrowserRunnerClient:
    """
    HTTP client for the AgentVerse Browser Runner.

    The runner owns Playwright, Chromium, Xvfb, and noVNC. This client only
    invokes its control-plane API.
    """
    def __init__(
        self,
        *,
        http_repository: Any,
        base_url: str,
        start_session_path: str = "/sessions/start",
        status_path_template: str = "/sessions/{session_id}/status",
        storage_state_path_template: str = "/sessions/{session_id}/storage-state",
        delete_session_path_template: str = "/sessions/{session_id}",
        render_page_path_template: str = "/sessions/{session_id}/render",
        ephemeral_render_path: str = "/render",
    ) -> None:
        self._http = http_repository
        self._base_url = base_url.rstrip("/")
        self._start_session_path = start_session_path
        self._status_path_template = status_path_template
        self._storage_state_path_template = storage_state_path_template
        self._delete_session_path_template = delete_session_path_template
        self._render_page_path_template = render_page_path_template
        self._ephemeral_render_path = ephemeral_render_path

    async def start_session(
        self,
        *,
        target_url: str,
        session_id: str | None = None,
        agentverse_browser_session_id: str | None = None,
        success_url_contains: str | None = None,
        callback_url: str | None = None,
        callback_token: str | None = None,
        auto_capture: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "target_url": target_url,
        }

        if session_id:
            payload["session_id"] = session_id

        if agentverse_browser_session_id:
            payload["agentverse_browser_session_id"] = agentverse_browser_session_id

        if success_url_contains:
            payload["success_url_contains"] = success_url_contains

        if callback_url:
            payload["callback_url"] = callback_url

        if callback_token:
            payload["callback_token"] = callback_token

        payload["auto_capture"] = auto_capture

        return await self._request(
            method="POST",
            path="/sessions/start",
            json=payload,
        )

    async def get_status(
        self,
        *,
        session_id: str,
    ) -> dict[str, Any]:
        return await self._request(
            method="GET",
            path=self._status_path_template.format(session_id=session_id),
        )

    async def get_storage_state(
        self,
        *,
        session_id: str,
    ) -> dict[str, Any]:
        return await self._request(
            method="GET",
            path=self._storage_state_path_template.format(session_id=session_id),
        )

    async def delete_session(
        self,
        *,
        session_id: str,
    ) -> dict[str, Any]:
        return await self._request(
            method="DELETE",
            path=self._delete_session_path_template.format(session_id=session_id),
        )
    
    async def render_page(
        self,
        *,
        runner_session_id: str,
        url: str,
        wait_until: str = "domcontentloaded",
        wait_ms: int = 2500,
        max_html_bytes: int = 5_000_000,
    ) -> dict[str, Any]:
        payload = {
            "url": url,
            "wait_until": wait_until,
            "wait_ms": wait_ms,
            "max_html_bytes": max_html_bytes,
        }

        return await self._request(
            method="POST",
            path=self._render_page_path_template.format(session_id=runner_session_id),
            json=payload,
        )

    async def render_ephemeral_page(
        self,
        *,
        url: str,
        wait_until: str = "domcontentloaded",
        wait_ms: int = 2500,
        max_html_bytes: int = 5_000_000,
    ) -> dict[str, Any]:
        return await self._request(
            method="POST",
            path=self._ephemeral_render_path,
            json={
                "url": url,
                "wait_until": wait_until,
                "wait_ms": wait_ms,
                "max_html_bytes": max_html_bytes,
            },
        )

    async def _request(
        self,
        *,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"

        try:
            payload = await self._http.fetch_json(
                url=url,
                method=method,
                json_payload=json,
            )
        except Exception as exc:
            raise BrowserRunnerClientError(
                f"Browser Runner request failed: {exc}"
            ) from exc

        if not isinstance(payload, dict):
            raise BrowserRunnerClientError(
                "Browser Runner response must be a JSON object."
            )

        return payload