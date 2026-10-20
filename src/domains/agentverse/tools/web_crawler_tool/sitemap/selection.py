from __future__ import annotations

from src.domains.agentverse.tools.web_crawler_tool.sitemap.models import (
    SitemapCatalog,
    SitemapCatalogNode,
    SitemapSelection,
)


def resolve_sitemap_selection(
    *,
    catalog: SitemapCatalog,
    selection: SitemapSelection,
) -> list[str]:
    selected: set[str] = set(
        selection.partition_urls
    )

    for node_path in selection.node_paths:
        node = _find_node(
            roots=catalog.roots,
            path=node_path,
        )

        if node is None:
            continue

        selected.update(
            _collect_partition_urls(
                node
            )
        )

    return sorted(
        selected
    )


def _find_node(
    *,
    roots: list[SitemapCatalogNode],
    path: list[str],
) -> SitemapCatalogNode | None:
    if not path:
        return None

    nodes = roots
    current = None

    for token in path:
        current = next(
            (
                node
                for node in nodes
                if node.token == token
            ),
            None,
        )

        if current is None:
            return None

        nodes = current.children

    return current


def _collect_partition_urls(
    node: SitemapCatalogNode,
) -> set[str]:
    urls = set(
        node.partition_urls
    )

    for child in node.children:
        urls.update(
            _collect_partition_urls(
                child
            )
        )

    return urls