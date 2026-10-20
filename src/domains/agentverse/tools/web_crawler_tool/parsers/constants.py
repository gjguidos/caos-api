from __future__ import annotations

CRAWLER_PARSE_QUEUE_PREFIX = (
    "agentverse:crawler:parse"
)

CRAWLER_PARSE_GROUP_PREFIX = (
    "crawler:parse"
)

CRAWLER_PARSE_SCHEMA = (
    "crawler.raw_page_parse_job.v1"
)

def parse_queue_name(
    run_id: str,
) -> str:
    return (
        f"{CRAWLER_PARSE_QUEUE_PREFIX}:"
        f"{run_id}"
    )


def parse_consumer_group(
    run_id: str,
) -> str:
    return (
        f"{CRAWLER_PARSE_GROUP_PREFIX}:"
        f"{run_id}"
    )