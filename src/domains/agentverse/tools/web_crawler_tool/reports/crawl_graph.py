from __future__ import annotations

from urllib.parse import urlparse

from src.domains.agentverse.tools.web_crawler_tool.types import (
    CrawlGraphEdge,
    CrawlGraphNode,
    CrawlGraphReport,
)


class CrawlGraphBuilder:
    def __init__(
        self,
        *,
        start_url: str,
    ):
        self.start_url = start_url

        self.nodes: dict[
            str,
            CrawlGraphNode,
        ] = {}

        self.edges: dict[
            tuple[str, str],
            CrawlGraphEdge,
        ] = {}

        self.add_node(
            url=start_url,
            depth=0,
            status="queued",
            discovered_from=None,
        )

    @classmethod
    def from_report(
        cls,
        *,
        start_url: str,
        report: CrawlGraphReport | None,
    ) -> CrawlGraphBuilder:
        builder = cls(
            start_url=start_url
        )

        if report is None:
            return builder

        for node in report.nodes:
            builder.add_node(
                url=node.url,
                depth=node.depth,
                status=node.status,
                discovered_from=node.discovered_from,
                title=node.title,
                status_code=node.status_code,
                content_type=node.content_type,
                error=node.error,
                metadata=dict(
                    node.metadata
                ),
            )

        for edge in report.edges:
            builder.add_edge(
                source_url=edge.source_url,
                target_url=edge.target_url,
                depth=edge.depth,
                status=edge.status,
                reason=edge.reason,
                metadata=dict(
                    edge.metadata
                ),
            )

        return builder
    
    def merge_report(
        self,
        report: CrawlGraphReport,
    ) -> None:
        for node in report.nodes:
            self.add_node(
                url=node.url,
                depth=node.depth,
                status=node.status,
                discovered_from=node.discovered_from,
                title=node.title,
                status_code=node.status_code,
                content_type=node.content_type,
                error=node.error,
                metadata=dict(
                    node.metadata
                ),
            )

        for edge in report.edges:
            self.add_edge(
                source_url=edge.source_url,
                target_url=edge.target_url,
                depth=edge.depth,
                status=edge.status,
                reason=edge.reason,
                metadata=dict(
                    edge.metadata
                ),
            )

    def add_node(
        self,
        *,
        url: str,
        depth: int,
        status: str = "queued",
        discovered_from: str | None = None,
        title: str = "",
        status_code: int | None = None,
        content_type: str | None = None,
        error: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        existing = self.nodes.get(url)

        if existing is None:
            self.nodes[url] = CrawlGraphNode(
                url=url,
                depth=depth,
                status=status,
                discovered_from=discovered_from,
                title=title,
                status_code=status_code,
                content_type=content_type,
                error=error,
                metadata=metadata or {},
            )
            return

        existing.depth = min(existing.depth, depth)

        # Prefer terminal / more informative statuses.
        existing.status = _prefer_status(existing.status, status)

        if title:
            existing.title = title
        if status_code is not None:
            existing.status_code = status_code
        if content_type:
            existing.content_type = content_type
        if error:
            existing.error = error
        if discovered_from and not existing.discovered_from:
            existing.discovered_from = discovered_from
        if metadata:
            existing.metadata.update(metadata)

    def add_edge(
        self,
        *,
        source_url: str,
        target_url: str,
        depth: int,
        status: str = "discovered",
        reason: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        edge_key = (
            source_url,
            target_url,
        )

        existing = self.edges.get(
            edge_key
        )

        if existing is None:
            self.edges[edge_key] = CrawlGraphEdge(
                source_url=source_url,
                target_url=target_url,
                depth=depth,
                status=status,
                reason=reason,
                metadata=metadata or {},
            )
            return

        existing.depth = min(
            existing.depth,
            depth,
        )

        existing.status = _prefer_edge_status(
            existing.status,
            status,
        )

        if reason:
            existing.reason = reason

        if metadata:
            existing.metadata.update(
                metadata
            )
        
    def mark_edge_followed(
        self,
        *,
        target_url: str,
    ) -> None:
        for edge in self.edges.values():
            if (
                edge.target_url == target_url
                and edge.status == "discovered"
            ):
                edge.status = "followed"

    def mark_visited(
        self,
        *,
        url: str,
        depth: int,
        title: str = "",
        status_code: int | None = None,
        content_type: str | None = None,
        error: str | None = None,
    ) -> None:
        self.add_node(
            url=url,
            depth=depth,
            status="error" if error else "visited",
            title=title,
            status_code=status_code,
            content_type=content_type,
            error=error,
        )

    def mark_skipped(
        self,
        *,
        url: str,
        depth: int,
        reason: str,
        discovered_from: str | None = None,
    ) -> None:
        self.add_node(
            url=url,
            depth=depth,
            status="skipped",
            discovered_from=discovered_from,
            error=reason,
        )

    def mark_blocked(
        self,
        *,
        url: str,
        depth: int,
        reason: str,
        discovered_from: str | None = None,
    ) -> None:
        self.add_node(
            url=url,
            depth=depth,
            status="blocked",
            discovered_from=discovered_from,
            error=reason,
        )

    def build(
        self,
    ) -> CrawlGraphReport:
        nodes = list(
            self.nodes.values()
        )

        edges = list(
            self.edges.values()
        )

        start_domain = (
            urlparse(
                self.start_url
            ).netloc.lower()
        )

        internal_edges = 0
        external_edges = 0

        for edge in edges:
            target_domain = (
                urlparse(
                    edge.target_url
                ).netloc.lower()
            )

            if (
                target_domain
                and target_domain
                == start_domain
            ):
                internal_edges += 1

            elif target_domain:
                external_edges += 1

        return CrawlGraphReport(
            node_count=len(nodes),
            edge_count=len(edges),

            visited_count=sum(
                1
                for node in nodes
                if node.status == "visited"
            ),

            queued_count=sum(
                1
                for node in nodes
                if node.status == "queued"
            ),

            skipped_count=sum(
                1
                for node in nodes
                if node.status == "skipped"
            ),

            blocked_count=sum(
                1
                for node in nodes
                if node.status == "blocked"
            ),

            error_count=sum(
                1
                for node in nodes
                if node.status == "error"
            ),

            max_depth_seen=max(
                (
                    node.depth
                    for node in nodes
                ),
                default=0,
            ),

            internal_edge_count=internal_edges,
            external_edge_count=external_edges,

            duplicate_edge_count=sum(
                1
                for edge in edges
                if edge.status == "duplicate"
            ),

            nodes=nodes,
            edges=edges,
        )


def _prefer_status(current: str, new: str) -> str:
    priority = {
        "queued": 0,
        "skipped": 1,
        "blocked": 2,
        "error": 3,
        "visited": 4,
    }

    return new if priority.get(new, 0) >= priority.get(current, 0) else current

def _prefer_edge_status(
    current: str,
    new: str,
) -> str:
    priority = {
        "discovered": 0,
        "duplicate": 1,
        "skipped": 2,
        "blocked": 3,
        "followed": 4,
    }

    return (
        new
        if priority.get(new, 0)
        >= priority.get(current, 0)
        else current
    )