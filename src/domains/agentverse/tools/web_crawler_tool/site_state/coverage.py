from __future__ import annotations

from datetime import UTC, datetime

from src.domains.agentverse.tools.web_crawler_tool.helpers.urls import (
    normalize_url,
)
from src.domains.agentverse.tools.web_crawler_tool.site_state.models import (
    CoverageRequest,
    CoverageResult,
    SiteState,
)


def resolve_site_coverage(
    *,
    state: SiteState,
    request: CoverageRequest,
) -> CoverageResult:
    root_url = normalize_url(
        request.root_url
    )

    now = datetime.now(UTC)

    reusable_urls: list[str] = []
    stale_urls: list[str] = []
    missing_urls: list[str] = []
    frontier_urls: list[str] = []
    reasons: list[str] = []

    graph = state.crawl_graph

    if graph is None:
        return CoverageResult(
            complete=False,
            reasons=[
                "crawl_graph_missing",
            ],
        )

    nodes_by_url = {
        normalize_url(node.url): node
        for node in graph.nodes
    }

    root_node = nodes_by_url.get(
        root_url
    )

    if root_node is None:
        return CoverageResult(
            complete=False,
            missing_urls=[
                root_url
            ],
            reasons=[
                "root_url_unknown",
            ],
        )

    scoped_nodes = _resolve_scoped_nodes(
        root_url=root_url,
        max_depth=request.max_depth,
        nodes_by_url=nodes_by_url,
        edges=graph.edges,
    )

    for url, relative_depth in scoped_nodes.items():
        if relative_depth > request.max_depth:
            continue

        page_state = state.pages.get(
            url
        )

        node = nodes_by_url.get(
            url
        )

        if page_state is None:
            if (
                node is not None
                and node.status in {
                    "queued",
                    "visited",
                }
            ):
                missing_urls.append(
                    url
                )

            continue

        age_seconds = (
            now
            - page_state.acquired_at
        ).total_seconds()

        if (
            age_seconds
            <= request.freshness_ttl_seconds
        ):
            reusable_urls.append(
                url
            )
        else:
            stale_urls.append(
                url
            )

    frontier_urls = _resolve_frontier_urls(
        root_url=root_url,
        max_depth=request.max_depth,
        scoped_nodes=scoped_nodes,
        nodes_by_url=nodes_by_url,
        edges=graph.edges,
    )

    if stale_urls:
        reasons.append(
            "stale_pages"
        )

    if missing_urls:
        reasons.append(
            "missing_pages"
        )

    if frontier_urls:
        reasons.append(
            "crawl_frontier_open"
        )

    if root_node.status != "visited":
        reasons.append(
            "root_not_successfully_visited"
        )

    complete = False

    if (
        not stale_urls
        and not missing_urls
        and not frontier_urls
        and root_node.status == "visited"
    ):
        reasons.append(
            "coverage_completeness_not_proven"
        )

    return CoverageResult(
        complete=complete,
        reusable_urls=sorted(
            set(reusable_urls)
        ),
        stale_urls=sorted(
            set(stale_urls)
        ),
        missing_urls=sorted(
            set(missing_urls)
        ),
        frontier_urls=sorted(
            set(frontier_urls)
        ),
        reasons=sorted(
            set(reasons)
        ),
    )

def _resolve_scoped_nodes(
    *,
    root_url: str,
    max_depth: int,
    nodes_by_url: dict,
    edges: list,
) -> dict[str, int]:
    adjacency: dict[str, set[str]] = {}

    for edge in edges:
        source = normalize_url(
            edge.source_url
        )
        target = normalize_url(
            edge.target_url
        )

        adjacency.setdefault(
            source,
            set(),
        ).add(
            target
        )

    discovered: dict[str, int] = {
        root_url: 0
    }

    queue: list[
        tuple[str, int]
    ] = [
        (
            root_url,
            0,
        )
    ]

    while queue:
        current_url, depth = (
            queue.pop(0)
        )

        if depth >= max_depth:
            continue

        for target_url in adjacency.get(
            current_url,
            set(),
        ):
            next_depth = depth + 1

            previous_depth = (
                discovered.get(
                    target_url
                )
            )

            if (
                previous_depth is not None
                and previous_depth <= next_depth
            ):
                continue

            discovered[
                target_url
            ] = next_depth

            queue.append(
                (
                    target_url,
                    next_depth,
                )
            )

    return discovered

def _resolve_frontier_urls(
    *,
    root_url: str,
    max_depth: int,
    scoped_nodes: dict[str, int],
    nodes_by_url: dict,
    edges: list,
) -> list[str]:
    adjacency: dict[str, set[str]] = {}

    for edge in edges:
        source = normalize_url(
            edge.source_url
        )
        target = normalize_url(
            edge.target_url
        )

        adjacency.setdefault(
            source,
            set(),
        ).add(
            target
        )

    frontier: list[str] = []

    for url, relative_depth in (
        scoped_nodes.items()
    ):
        if relative_depth >= max_depth:
            continue

        node = nodes_by_url.get(
            url
        )

        if node is None:
            frontier.append(
                url
            )
            continue

        if node.status == "queued":
            frontier.append(
                url
            )
            continue

        if node.status in {
            "error",
            "blocked",
        }:
            frontier.append(
                url
            )
            continue

        children = adjacency.get(
            url,
            set(),
        )

        for child_url in children:
            if (
                child_url
                not in scoped_nodes
            ):
                frontier.append(
                    child_url
                )

    return frontier