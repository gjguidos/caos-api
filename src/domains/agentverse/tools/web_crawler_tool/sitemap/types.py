from typing import Literal, TypeAlias

SitemapDiscoveryRequestMode: TypeAlias = Literal[
    "planned",
    "full",
]


SitemapDiscoveryMode: TypeAlias = Literal[
    "planned",
    "full",
]


SitemapParseProfile = Literal[
    "locations",
    "rich",
]


SitemapSelectionScope: TypeAlias = Literal[
    "all",
    "selected",
]

SitemapTraversalPolicy: TypeAlias = Literal[
    "complete",
    "bounded",
]

max_sitemaps: int | None = None

page_acquisition_limit: int | None = None