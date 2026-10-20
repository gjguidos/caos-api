from __future__ import annotations

from dataclasses import dataclass

import httpx

DEFAULT_SITEMAP_MAX_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class SitemapFetchResult:
    url: str
    final_url: str

    status_code: int
    content_type: str | None

    text: str | None

    not_modified: bool = False

    etag: str | None = None
    last_modified: str | None = None


class SitemapFetcher:
    """
    Acquires sitemap documents.

    Responsibilities:
      - HTTP acquisition
      - redirect handling
      - timeout enforcement
      - response size limits
      - basic response validation
      - preservation of HTTP validators

    Does not:
      - parse sitemap XML
      - resolve sitemap indexes
      - recurse into child sitemaps
      - perform crawl planning
    """

    def __init__(
        self,
        *,
        timeout_seconds: int = 15,
        max_response_bytes: int = DEFAULT_SITEMAP_MAX_BYTES,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.headers = headers or {}
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        """Return the run-scoped client, creating it on first use.

        AsyncClient maintains a connection pool, so concurrent sitemap
        requests can reuse connections instead of paying the TCP/TLS setup
        cost for every sitemap file.
        """
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_seconds),
                follow_redirects=True,
                headers={
                    "User-Agent": "AgentVerse-WebCrawler/0.1",
                    **self.headers,
                },
            )

        return self._client

    async def aclose(self) -> None:
        """Close the pooled HTTP connections when the discovery run ends."""
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()

    async def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> SitemapFetchResult:
        request_headers: dict[str, str] = {}
        if etag:
            request_headers["If-None-Match"] = etag

        if last_modified:
            request_headers["If-Modified-Since"] = last_modified

        client = self._get_client()
        async with client.stream(
            "GET",
            url,
            headers=request_headers,
        ) as response:
            content_length = response.headers.get(
                "content-length"
            )

            if content_length:
                try:
                    declared_size = int(
                        content_length
                    )
                except ValueError:
                    declared_size = None

                if (
                    declared_size is not None
                    and declared_size > self.max_response_bytes
                ):
                    raise ValueError(
                        "Sitemap response exceeds "
                        f"{self.max_response_bytes} bytes"
                    )

            if response.status_code == 304:
                return SitemapFetchResult(
                    url=url,
                    final_url=str(response.url),
                    status_code=304,
                    content_type=response.headers.get(
                        "content-type"
                    ),
                    text=None,
                    not_modified=True,
                    etag=(
                        response.headers.get("etag")
                        or etag
                    ),
                    last_modified=(
                        response.headers.get(
                            "last-modified"
                        )
                        or last_modified
                    ),
                )

            response.raise_for_status()

            content = await self._read_bounded(
                response
            )

            encoding = (
                response.encoding
                or "utf-8"
            )

            text = content.decode(
                encoding,
                errors="replace",
            )

            return SitemapFetchResult(
                url=url,
                final_url=str(response.url),
                status_code=response.status_code,
                content_type=response.headers.get(
                    "content-type"
                ),
                text=text,
                not_modified=False,
                etag=response.headers.get(
                    "etag"
                ),
                last_modified=response.headers.get(
                    "last-modified"
                ),
            )

    async def _read_bounded(
        self,
        response: httpx.Response,
    ) -> bytes:
        chunks: list[bytes] = []
        total = 0

        async for chunk in response.aiter_bytes():
            total += len(chunk)

            if total > self.max_response_bytes:
                raise ValueError(
                    "Sitemap response exceeds "
                    f"{self.max_response_bytes} bytes"
                )

            chunks.append(chunk)

        return b"".join(chunks)
    
