from __future__ import annotations

from src.domains.agentverse.tools.web_crawler_tool.analysis.models import (
    PageAnalysis,
)
from src.domains.agentverse.tools.web_crawler_tool.contributions.models import (
    ReportContributionPayload,
)
from src.domains.agentverse.tools.web_crawler_tool.contributions.page_contribution import (
    PageContribution,
)
from src.domains.agentverse.tools.web_crawler_tool.execution.models import (
    ProcessedPagePayload,
    ProcessedPageRecord,
)


def build_analysis_contribution(
    *,
    payload: ProcessedPagePayload,
    record: ProcessedPageRecord,
    analysis: PageAnalysis,
) -> ReportContributionPayload:

    contribution = PageContribution()

    contribution.increment(
        "page_totals.page_count"
    )

    media_items = analysis.media_items

    if media_items:
        contribution.increment(
            "reports.media.pages_with_media"
        )

        contribution.increment(
            "reports.media.total_media",
            len(media_items),
        )

        contribution.media_items.extend(
            media_items
        )

    return ReportContributionPayload(
        run_id=payload.run_id,
        page_id=record.page_id,
        url_hash=payload.url_hash,
        lease_id=payload.lease_id,
        source_page_url=analysis.final_url,
        increments=contribution.increments,
        add_to_sets=contribution.add_to_sets,
        media_items=contribution.media_items,
    )