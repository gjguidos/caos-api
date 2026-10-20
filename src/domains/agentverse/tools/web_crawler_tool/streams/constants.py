from __future__ import annotations

CRAWLER_ACQUISITION_QUEUE_PREFIX = (
    "agentverse:crawler:acquisition"
)

CRAWLER_ACQUISITION_GROUP_PREFIX = (
    "crawler"
)

CRAWLER_ACQUISITION_SCHEMA = (
    "crawler.sitemap_chunk_job.v1"
)


def acquisition_queue_name(run_id: str) -> str:
    return f"{CRAWLER_ACQUISITION_QUEUE_PREFIX}:{run_id}"


def acquisition_consumer_group(run_id: str) -> str:
    return f"{CRAWLER_ACQUISITION_GROUP_PREFIX}:{run_id}"