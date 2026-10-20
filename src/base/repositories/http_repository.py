from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from src.base.infrastructure.http.async_http_client import (
    AsyncHttpClient,
    HttpClientError,
)

logger = logging.getLogger(__name__)


class HttpRepository:
    """
    Repository wrapper for AsyncHttpClient.
    Provides high-level methods for common HTTP operations.
    """

    def __init__(self, client: AsyncHttpClient):
        self.client = client

        # Centralized dispatch table
        self._method_map: dict[str, Callable[..., Awaitable[dict[str, Any]]]] = {
            "GET": self.client.get,
            "POST": self.client.post,
            "PUT": self.client.put,
            "PATCH": self.client.patch,
            "DELETE": self.client.delete,
        }

    # ------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------

    def _resolve_method(self, method: str) -> Callable[..., Awaitable[dict[str, Any]]]:
        method_upper = method.upper()
        if method_upper not in self._method_map:
            raise ValueError(f"Unsupported HTTP method: {method}")
        return self._method_map[method_upper]

    # ------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------

    async def fetch_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        method: str = "GET",
        json_payload: Any | None = None,
    ) -> dict[str, Any]:
        """
        Fetch and parse JSON response.
        """
        logger.info(f"Fetching JSON via {method.upper()}: {url} with params={params}")

        try:
            request_fn = self._resolve_method(method)

            response = await request_fn(
                url,
                params=params,
                headers=headers,
                json=json_payload,
            )
            return response

        except HttpClientError as e:
            logger.error(f"HTTP request failed: {str(e)}")
            raise

    async def fetch_text(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        """
        Fetch raw text response.
        """
        logger.info(f"Fetching text: {url} with params={params}")

        try:
            response = await self.client.get(url, params=params, headers=headers)

            # Non-JSON response returned by AsyncHttpClient
            if isinstance(response, dict) and "text" in response:
                return response["text"]

            # Convert JSON response to a string
            return json.dumps(response)

        except HttpClientError as e:
            logger.error(f"HTTP request failed: {str(e)}")
            raise
