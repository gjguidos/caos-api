from __future__ import annotations

import hashlib
import json
from typing import Any

from src.domains.agentverse.tools.web_crawler_tool.engine.helpers import (
    strip_fragment,
)
from src.domains.agentverse.tools.web_crawler_tool.runtime import (
    ResolvedCrawlerRuntime,
)


def build_acquisition_identity(
    *,
    runtime: ResolvedCrawlerRuntime,
    start_url: str,
    crawl_mode: str,
    target_urls: list[str] | None = None,
    acquisition_limit: int | None = None,
) -> dict[str, Any]:
    """
    Describe acquisition inputs independently of report projection.

    target_urls=None means ordinary frontier crawling.
    target_urls=[] means explicit acquisition with no targets.
    """

    identity: dict[str, Any] = {
        "start_url": start_url,
        "crawl_mode": crawl_mode,
        "auth_mode": runtime.auth_mode,
        "browser_session_id": runtime.params.browser_session_id,
        "max_pages": runtime.max_pages,
        "max_depth": runtime.max_depth,
        "crawl_strategy": runtime.crawl_strategy,
        "same_domain_only": runtime.same_domain_only,
        "include_links": runtime.include_links,
        "max_chars_per_page": runtime.max_chars_per_page,
        "timeout_seconds": runtime.timeout_seconds,
    }

    if target_urls is None:
        if acquisition_limit is not None:
            raise ValueError(
                "acquisition_limit requires explicit target_urls."
            )

        return identity

    if acquisition_limit is None or acquisition_limit < 0:
        raise ValueError(
            "Explicit target acquisition requires a non-negative "
            "acquisition_limit."
        )

    normalized_targets = list(
        dict.fromkeys(
            clean_url
            for url in target_urls
            if (clean_url := strip_fragment(url))
        )
    )

    identity.update(
        {
            "acquisition_kind": "explicit_targets",
            "target_urls": normalized_targets,
            "max_pages": acquisition_limit,
            "max_depth": 0,
        }
    )

    # Explicit targets execute in supplied order without link expansion.
    identity.pop("crawl_strategy")

    return identity


def build_acquisition_fingerprint(
    acquisition: dict[str, Any],
) -> str:
    """
    Produces a stable SHA-256 fingerprint for an acquisition identity.
    """

    encoded = json.dumps(
        acquisition,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")

    return hashlib.sha256(encoded).hexdigest()

def resolve_acquisition_fingerprint(
    *,
    runtime: ResolvedCrawlerRuntime,
    start_url: str,
    crawl_mode: str,
    target_urls: list[str] | None = None,
    acquisition_limit: int | None = None,
) -> tuple[dict[str, Any], str]:
    acquisition = build_acquisition_identity(
        runtime=runtime,
        start_url=start_url,
        crawl_mode=crawl_mode,
        target_urls=target_urls,
        acquisition_limit=acquisition_limit,
    )

    fingerprint = build_acquisition_fingerprint(
        acquisition
    )

    return acquisition, fingerprint