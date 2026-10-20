from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import httpx

from src.domains.agentverse.tools.web_crawler_tool.auth.context import (
    CrawlAuthContext,
)
from src.domains.agentverse.tools.web_crawler_tool.config import (
    WebCrawlerSecurityConfig,
)
from src.domains.agentverse.tools.web_crawler_tool.fetchers.models import (
    FetchedPage,
)
from src.domains.agentverse.tools.web_crawler_tool.security.policy import (
    CrawlerSecurityError,
    enforce_url_security,
)


class StaticPageFetcher:
    """Fetch and validate responses without parsing their HTML.

    HTML parsing and report generation belong to the parser worker. This
    class only performs network I/O and returns the raw response bytes.
    """

    def __init__(
        self,
        *,
        timeout_seconds: int,
        user_agent: str,
        allowed_content_types: list[str],
        max_response_bytes: int,
        security: WebCrawlerSecurityConfig,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError(
                "timeout_seconds must be positive."
            )
        if max_response_bytes <= 0:
            raise ValueError(
                "max_response_bytes must be positive."
            )

        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent
        self.allowed_content_types = allowed_content_types
        self.max_response_bytes = max_response_bytes
        self.security = security
        self._client = client

    async def fetch(
        self,
        url: str,
        *,
        context: dict[str, Any] | None = None,
        auth_context: CrawlAuthContext | None = None,
        agent: Any | None = None,
    ) -> FetchedPage:
        del context, auth_context, agent
        if self._client is not None:
            # The operation owns and closes this shared client.
            result = await self._fetch_with_client(
                url,
                self._client,
            )

            return result
        
        # Compatibility path for callers without an injected client.
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=False,
            headers={"User-Agent": self.user_agent},
            limits=httpx.Limits(
                max_connections=64,
                max_keepalive_connections=64,
            ),
        ) as client:
            result = await self._fetch_with_client(url, client)
            return result

    async def _fetch_with_client(
        self,
        url: str,
        client: httpx.AsyncClient,
    ) -> FetchedPage:
        current_url = url
        redirects = 0

        while True:
            response = await client.get(
                current_url,
                timeout=self.timeout_seconds,
                headers={"User-Agent": self.user_agent},
                # Redirects are validated explicitly below.
                follow_redirects=False,
            )

            if response.status_code not in {
                301,
                302,
                303,
                307,
                308,
            }:
                break

            if redirects >= self.security.max_redirects:
                return self._error_page(
                    url=url,
                    final_url=current_url,
                    response=response,
                    error="too_many_redirects",
                )

            location = response.headers.get("location")

            if not location:
                break

            next_url = urljoin(current_url, location)

            try:
                enforce_url_security(
                    next_url,
                    security=self.security,
                    start_url=url,
                )
            except CrawlerSecurityError:
                return self._error_page(
                    url=url,
                    final_url=next_url,
                    response=response,
                    error="blocked_redirect",
                )

            current_url = next_url
            redirects += 1

        content_type = response.headers.get(
            "content-type",
            "",
        ).lower()
        final_url = str(response.url)

        content_length = response.headers.get("content-length")
        if content_length:
            try:
                declared_size = int(content_length)
            except ValueError:
                declared_size = None

            if (
                declared_size is not None
                and declared_size > self.max_response_bytes
            ):
                return self._error_page(
                    url=url,
                    final_url=final_url,
                    response=response,
                    error="response_too_large",
                )

        if not any(
            allowed in content_type
            for allowed in self.allowed_content_types
        ):
            return self._error_page(
                url=url,
                final_url=final_url,
                response=response,
                error="unsupported_content_type",
            )

        body = response.content

        if len(body) > self.max_response_bytes:
            return self._error_page(
                url=url,
                final_url=final_url,
                response=response,
                error="response_too_large",
            )

        return FetchedPage(
            url=url,
            final_url=final_url,
            status_code=response.status_code,
            content_type=content_type,
            encoding=response.encoding,
            body=body,
            error=None,
        )

    @staticmethod
    def _error_page(
        *,
        url: str,
        final_url: str,
        response: httpx.Response,
        error: str,
    ) -> FetchedPage:
        return FetchedPage(
            url=url,
            final_url=final_url,
            status_code=response.status_code,
            content_type=response.headers.get(
                "content-type",
                "",
            ).lower(),
            encoding=response.encoding,
            body=b"",
            error=error,
        )
