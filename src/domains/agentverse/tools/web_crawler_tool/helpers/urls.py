from __future__ import annotations

from urllib.parse import (
    urldefrag,
    urljoin,
    urlparse,
)

HTTP_SCHEMES = {
    "http",
    "https",
}

SKIPPED_LINK_SCHEMES = (
    "mailto:",
    "tel:",
    "javascript:",
    "data:",
    "blob:",
)


class UrlValidationError(ValueError):
    pass


def normalize_url(
    url: str,
) -> str:
    """
    Normalize and validate an absolute HTTP(S) URL.

    Adds https:// when no scheme is supplied.

    This validates URL structure only. It does not perform
    network/security policy checks.
    """

    value = (
        url
        or ""
    ).strip()

    if not value:
        raise UrlValidationError(
            "URL is required."
        )

    parsed = urlparse(
        value
    )

    if not parsed.scheme:
        value = (
            f"https://{value}"
        )

        parsed = urlparse(
            value
        )

    if parsed.scheme.lower() not in HTTP_SCHEMES:
        raise UrlValidationError(
            "Only http and https URLs are supported."
        )

    if not parsed.hostname:
        raise UrlValidationError(
            "URL must include a valid host."
        )

    value, _ = urldefrag(
        value
    )

    return value


def resolve_http_url(
    base_url: str,
    raw_url: str,
) -> str | None:
    """
    Resolve a discovered link against its source page.

    Returns an absolute HTTP(S) URL, or None when the link
    is not crawlable.
    """

    if not raw_url:
        return None

    value = raw_url.strip()

    if not value:
        return None

    if value.lower().startswith(
        SKIPPED_LINK_SCHEMES
    ):
        return None

    resolved = urljoin(
        base_url,
        value,
    )

    resolved, _ = urldefrag(
        resolved
    )

    parsed = urlparse(
        resolved
    )

    if parsed.scheme.lower() not in HTTP_SCHEMES:
        return None

    if not parsed.hostname:
        return None

    return resolved


def same_domain(
    url: str,
    start_url: str,
) -> bool:
    return (
        urlparse(url).netloc.lower()
        == urlparse(start_url).netloc.lower()
    )