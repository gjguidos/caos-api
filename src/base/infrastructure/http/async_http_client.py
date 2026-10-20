from __future__ import annotations

import asyncio
import json as jsonlib
import logging
from typing import Any

import aiohttp

from src.base.infrastructure.http.http_client_config import HttpClientConfig

logger = logging.getLogger(__name__)


class HttpClientError(Exception):
    """Base exception for HTTP client errors."""

    def __init__(self, message: str, status_code: int | None = None, response_data: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_data = response_data


class AsyncHttpClient:
    """
    Reusable async HTTP client for external API calls using aiohttp.
    """

    def __init__(self, config: HttpClientConfig | None = None):
        self.config = config or HttpClientConfig()
        logger.debug(
            f"HTTP client initialized: base_url={self.config.base_url}, "
            f"timeout={self.config.timeout}s, max_retries={self.config.max_retries}"
        )

    # ------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------

    def _build_url(self, url: str) -> str:
        if self.config.base_url and not url.startswith(("http://", "https://")):
            return f"{self.config.base_url.rstrip('/')}/{url.lstrip('/')}"
        return url

    def _merge_headers(self, headers: dict[str, str] | None) -> dict[str, str]:
        return {**self.config.headers, **(headers or {})}

    def _log_http_error(self, method: str, url: str, status: int):
        if status in (401, 403, 404, 429, 500, 503):
            level = logging.ERROR if status in (401, 403, 500, 503) else logging.WARNING
            logger.log(level, f"HTTP {method} {url} failed: {status}")
        else:
            logger.error(f"HTTP {method} {url} failed: {status}")

    @staticmethod
    def _upstream_detail(response_text: str) -> str | None:
        """The reason the upstream service gave, if its body carries one.

        Without this the reason is only reachable through `response_data`, which
        no caller reads, so an upstream error arrives as a canned status string.
        """
        try:
            body = jsonlib.loads(response_text)
        except Exception:
            return None

        if not isinstance(body, dict):
            return None

        for key in ("detail", "error", "message"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        return None

    def _handle_http_error(self, method: str, url: str, response_text: str, status: int):
        messages = {
            401: "Authentication failed: Invalid or missing credentials",
            403: "Access forbidden: Insufficient permissions",
            404: "Resource not found",
            429: "Rate limit exceeded",
            500: "Internal server error",
            503: "Service unavailable",
        }
        message = self._upstream_detail(response_text) or messages.get(
            status, f"HTTP error {status}"
        )

        self._log_http_error(method, url, status)

        raise HttpClientError(message, status_code=status, response_data=response_text)

    def _should_retry(self, attempt: int) -> bool:
        return attempt < self.config.max_retries

    async def _parse_response(self, response: aiohttp.ClientResponse, body: str) -> dict[str, Any]:
        try:
            return await response.json()
        except Exception:
            return {"text": body, "status": response.status}

    # ------------------------------------------------------------
    # Core request logic
    # ------------------------------------------------------------

    async def _make_request(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        full_url = self._build_url(url)
        merged_headers = self._merge_headers(headers)

        timeout = aiohttp.ClientTimeout(total=self.config.timeout)
        ssl_context = None if self.config.verify_ssl else False

        attempt = 0

        while True:
            try:
                async with aiohttp.ClientSession(
                    timeout=timeout,
                    connector=aiohttp.TCPConnector(ssl=ssl_context),
                ) as session:
                    async with session.request(
                        method=method,
                        url=full_url,
                        params=params,
                        json=json,
                        headers=merged_headers,
                        allow_redirects=self.config.follow_redirects,
                        **kwargs,
                    ) as response:
                        response_text = await response.text()

                        if response.status >= 400:
                            self._handle_http_error(
                                method, full_url, response_text, response.status
                            )

                        return await self._parse_response(response, response_text)

            except (aiohttp.ClientError, TimeoutError) as e:
                attempt += 1

                if not self._should_retry(attempt):
                    logger.error(
                        f"HTTP {method} {full_url} failed after {attempt} attempts: {type(e).__name__}: {str(e)}"
                    )
                    raise HttpClientError(
                        f"Request failed after {attempt} attempts: {str(e)}"
                    ) from e

                backoff = self.config.retry_backoff * (2**attempt)
                logger.warning(
                    f"HTTP {method} {full_url} failed (attempt {attempt}/{self.config.max_retries + 1}): "
                    f"{type(e).__name__}: {str(e)}. Retrying in {backoff}s"
                )
                await asyncio.sleep(backoff)

            except HttpClientError:
                # Don't retry on known HTTP errors
                raise

    # ------------------------------------------------------------
    # Public API shortcuts
    # ------------------------------------------------------------

    async def get(self, url: str, params=None, headers=None, **kwargs):
        return await self._make_request("GET", url, params=params, headers=headers, **kwargs)

    async def post(self, url: str, json=None, params=None, headers=None, **kwargs):
        return await self._make_request(
            "POST", url, params=params, json=json, headers=headers, **kwargs
        )

    async def put(self, url: str, json=None, params=None, headers=None, **kwargs):
        return await self._make_request(
            "PUT", url, params=params, json=json, headers=headers, **kwargs
        )

    async def delete(self, url: str, params=None, headers=None, **kwargs):
        return await self._make_request("DELETE", url, params=params, headers=headers, **kwargs)

    async def patch(self, url: str, json=None, params=None, headers=None, **kwargs):
        return await self._make_request(
            "PATCH", url, params=params, json=json, headers=headers, **kwargs
        )
