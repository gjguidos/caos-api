from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from src.domains.agentverse.tools.web_crawler_tool.sitemap.models import (
    SitemapTopology,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.resolver import (
    SitemapResolver,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.store import (
    SitemapResourceState,
    SitemapStateStore,
)


@dataclass(frozen=True)
class ResolvedSitemapEvidence:
    topology: SitemapTopology

    source: str
    reused: bool

    resource_url: str
    final_url: str

    etag: str | None
    last_modified: str | None

    evidence_created_at: datetime | None = None
    
    not_modified: bool = False

    @property
    def evidence_age_seconds(self) -> float | None:
        if self.evidence_created_at is None:
            return None

        return max(
            0.0,
            (
                datetime.now(UTC)
                - self.evidence_created_at
            ).total_seconds(),
        )


async def resolve_sitemap_evidence(
    *,
    url: str,
    resolver: SitemapResolver,
    store: SitemapStateStore | None,
    freshness_ttl_seconds: int,
) -> ResolvedSitemapEvidence:
    now = datetime.now(UTC)

    cached = (
        await store.get(url)
        if store is not None
        else None
    )

    if cached is not None:
        age_seconds = (
            now - cached.acquired_at
        ).total_seconds()

        if age_seconds <= freshness_ttl_seconds:
            # Fresh cached evidence.
            return ResolvedSitemapEvidence(
                topology=cached.topology,
                source="cache",
                reused=True,
                resource_url=url,
                final_url=cached.final_url,
                etag=cached.etag,
                last_modified=cached.last_modified,
                evidence_created_at=cached.acquired_at,
                not_modified=False,
            )

    resolved = await resolver.resolve(
        url,
        etag=(
            cached.etag
            if cached is not None
            else None
        ),
        last_modified=(
            cached.last_modified
            if cached is not None
            else None
        ),
    )

    if resolved.not_modified:
        if cached is None:
            raise ValueError(
                "Sitemap returned 304 but no cached topology "
                f"is available for {url}"
            )

        refreshed = SitemapResourceState(
            url=cached.url,
            final_url=cached.final_url,
            acquired_at=now,
            etag=(
                resolved.fetch.etag
                or cached.etag
            ),
            last_modified=(
                resolved.fetch.last_modified
                or cached.last_modified
            ),
            topology=cached.topology,
        )

        if store is not None:
            await store.put(
                refreshed
            )

        return ResolvedSitemapEvidence(
            topology=refreshed.topology,
            source="conditional_cache",
            reused=True,
            resource_url=url,
            final_url=refreshed.final_url,
            etag=refreshed.etag,
            last_modified=refreshed.last_modified,
            evidence_created_at=refreshed.acquired_at,
            not_modified=True,
        )

    if resolved.topology is None:
        raise ValueError(
            f"Sitemap resolution produced no topology: {url}"
        )

    state = SitemapResourceState(
        url=url,
        final_url=resolved.final_url,
        acquired_at=now,
        etag=resolved.fetch.etag,
        last_modified=resolved.fetch.last_modified,
        topology=resolved.topology,
    )

    if store is not None:
        await store.put(
            state
        )

    return ResolvedSitemapEvidence(
        topology=resolved.topology,
        source="network",
        reused=False,
        resource_url=url,
        final_url=resolved.final_url,
        etag=resolved.fetch.etag,
        last_modified=resolved.fetch.last_modified,
        evidence_created_at=now,
        not_modified=False,
    )