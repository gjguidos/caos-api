from __future__ import annotations

CRAWLER_REPORT_QUEUE_PREFIX = (
    "agentverse:crawler:report"
)

CRAWLER_REPORT_GROUP_PREFIX = (
    "crawler:report"
)


def report_queue_name(
    run_id: str,
) -> str:
    return (
        f"{CRAWLER_REPORT_QUEUE_PREFIX}:{run_id}"
    )


def report_consumer_group(
    run_id: str,
) -> str:
    return (
        f"{CRAWLER_REPORT_GROUP_PREFIX}:{run_id}"
    )