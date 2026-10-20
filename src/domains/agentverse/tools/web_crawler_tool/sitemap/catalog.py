from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse

from src.domains.agentverse.tools.web_crawler_tool.sitemap.models import (
    SitemapCatalog,
    SitemapCatalogNode,
    SitemapPartition,
    SitemapPartitionDescriptor,
    SitemapTopology,
)


_TOKEN_SPLIT_RE = re.compile(
    r"[-_.]+"
)

_NUMERIC_SUFFIX_RE = re.compile(
    r"^(?P<base>.*?)[-_](?P<number>\d+)$"
)


def build_sitemap_catalog(
    *,
    topology: SitemapTopology,
) -> SitemapCatalog:
    descriptors = [
        describe_partition(
            partition=partition
        )
        for partition in topology.partitions
    ]

    roots: dict[
        str,
        dict[str, Any],
    ] = {}

    for descriptor in descriptors:
        structural_tokens = list(
            descriptor.tokens
        )

        if (
            descriptor.numeric_suffix is not None
            and structural_tokens
        ):
            structural_tokens = (
                structural_tokens[:-1]
            )

        if not structural_tokens:
            continue

        current = roots

        for depth, token in enumerate(
            structural_tokens
        ):
            node = current.setdefault(
                token,
                {
                    "token": token,
                    "depth": depth,
                    "partition_count": 0,
                    "partition_urls": [],
                    "children": {},
                },
            )

            node[
                "partition_count"
            ] += 1

            current = node[
                "children"
            ]

        # Only store the actual partition URL at
        # the deepest structural node.
        node[
            "partition_urls"
        ].append(
            descriptor.url
        )

    return SitemapCatalog(
        root_url=topology.root_url,
        partition_count=len(
            descriptors
        ),
        roots=[
            _build_catalog_node(
                raw=node
            )
            for _, node in sorted(
                roots.items()
            )
        ],
    )


def describe_partition(
    *,
    partition: SitemapPartition,
) -> SitemapPartitionDescriptor:
    parsed = urlparse(
        partition.url
    )

    path = PurePosixPath(
        parsed.path
    )

    filename = path.name

    stem = (
        filename.removesuffix(
            path.suffix
        )
        if path.suffix
        else filename
    )

    path_segments = [
        segment
        for segment in path.parts
        if segment not in {
            "",
            "/",
        }
    ]

    tokens = _tokenize_stem(
        stem
    )

    numeric_suffix = (
        _extract_numeric_suffix(
            stem
        )
    )

    return SitemapPartitionDescriptor(
        url=partition.url,
        filename=filename,
        stem=stem,
        path_segments=path_segments,
        tokens=tokens,
        numeric_suffix=numeric_suffix,
    )


def _build_catalog_node(
    *,
    raw: dict[str, Any],
) -> SitemapCatalogNode:
    children = raw.get(
        "children"
    ) or {}

    return SitemapCatalogNode(
        token=str(
            raw["token"]
        ),
        depth=int(
            raw["depth"]
        ),
        partition_count=int(
            raw["partition_count"]
        ),
        partition_urls=list(
            raw.get(
                "partition_urls"
            )
            or []
        ),
        children=[
            _build_catalog_node(
                raw=child
            )
            for _, child in sorted(
                children.items()
            )
        ],
    )


def _tokenize_stem(
    stem: str,
) -> list[str]:
    return [
        token
        for token in _TOKEN_SPLIT_RE.split(
            stem
        )
        if token
    ]


def _extract_numeric_suffix(
    stem: str,
) -> int | None:
    match = _NUMERIC_SUFFIX_RE.match(
        stem
    )

    if not match:
        return None

    try:
        return int(
            match.group(
                "number"
            )
        )
    except ValueError:
        return None