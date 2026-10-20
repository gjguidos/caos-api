from __future__ import annotations

from typing import Any


def build_crawler_markdown(
    *,
    start_url: str,
    mode: str,
    pages: list[dict[str, Any]],
    reports: dict[str, Any],
    errors: list[str] | None = None,
    report: str | None = None,
    find: str | None = None,
    matches: list[dict[str, Any]] | None = None,
    security_audit: list[dict[str, Any]] | None = None,
) -> str:
    errors = errors or []
    matches = matches or []
    security_audit = security_audit or []

    metadata = reports.get("metadata") or {}
    evidence = metadata.get("evidence") or {}
    coverage = metadata.get("coverage") or {}
    evidence_origin = evidence.get("origin")
    evidence_label = _evidence_origin_label(
        evidence_origin
    )

    crawl_summary = reports.get("crawl_summary") or {}
    summary_coverage = (
        crawl_summary.get("coverage")
        or {}
    )
    

    timing = metadata.get("timing") or {}
    limits = metadata.get("limits") or {}
    flags = metadata.get("flags") or {}

    output_profile = metadata.get("output_profile")
    output_format = metadata.get("output_format")
    crawl_strategy = metadata.get("crawl_strategy")
    requested_reports = metadata.get("requested_reports") or []

    page_totals = crawl_summary.get("page_totals") or {}
    content_totals = crawl_summary.get("content_totals") or {}
    html_block_totals = crawl_summary.get("html_block_totals") or {}
    link_totals = crawl_summary.get("link_totals") or {}
    document_totals = crawl_summary.get("document_totals") or {}
    media_totals = crawl_summary.get("media_totals") or {}
    form_totals = crawl_summary.get("form_totals") or {}
    seo_totals = crawl_summary.get("seo_totals") or {}
    error_totals = crawl_summary.get("error_totals") or {}
    

    lines: list[str] = []

    lines.append("# Web Crawl Result")
    lines.append("")
    lines.append(f"**Start URL:** {start_url}")
    lines.append(f"**Mode:** {mode}")

    lines.append(
        f"**Evidence source:** {evidence_label}"
    )

    if output_profile:
        lines.append(f"**Output profile:** {output_profile}")

    if output_format:
        lines.append(f"**Output format:** {output_format}")

    if crawl_strategy:
        lines.append(f"**Crawl strategy:** {crawl_strategy}")

    lines.append(f"**Report:** {report or 'summary'}")
    lines.append(f"**Pages crawled:** {len(pages)}")

    started_at = timing.get("started_at")
    finished_at = timing.get("finished_at")
    duration_seconds = timing.get("duration_seconds")
    timezone = timing.get("timezone")

    if started_at:
        lines.append(f"**Started at:** {started_at}")

    if finished_at:
        lines.append(f"**Finished at:** {finished_at}")

    if duration_seconds is not None:
        lines.append(f"**Duration:** {duration_seconds}s")

    if timezone:
        lines.append(f"**Timezone:** {timezone}")

    if find:
        lines.append(f"**Find:** {find}")
        lines.append(f"**Matches:** {len(matches)}")

    if errors:
        lines.append(f"**Errors:** {len(errors)}")

    lines.append("")

    _append_report_metadata(
        lines=lines,
        metadata=metadata,
        requested_reports=requested_reports,
        limits=limits,
        flags=flags,
        evidence=evidence,
        coverage=coverage,
    )

    _append_crawl_summary(
        lines=lines,
        page_totals=page_totals,
        content_totals=content_totals,
        html_block_totals=html_block_totals,
        link_totals=link_totals,
        document_totals=document_totals,
        media_totals=media_totals,
        form_totals=form_totals,
        seo_totals=seo_totals,
        error_totals=error_totals,
        coverage=summary_coverage,
    )

    _append_filters_summary(
        lines=lines,
        filters_report=reports.get("filters") or {},
    )

    _append_find_summary(
        lines=lines,
        find_report=reports.get("find") or {},
        fallback_find=find,
        fallback_matches=matches,
    )

    _append_pages_summary(lines=lines, pages=pages)

    _append_available_reports(lines=lines, reports=reports)

    # Keep markdown readable by default.
    # Full match evidence remains available in result.reports.find and result.matches.
    show_match_details = bool(
        metadata.get("markdown", {}).get("show_match_details", False)
    )

    if show_match_details and matches:
        _append_matches(lines=lines, matches=matches)

    if errors or security_audit:
        _append_errors_and_security(
            lines=lines,
            errors=errors,
            security_audit=security_audit,
        )

    lines.append("")
    lines.append("Detailed structured data is available in `result.reports`.")

    return "\n".join(lines).strip()


def _append_filters_summary(
    *,
    lines: list[str],
    filters_report: dict[str, Any],
) -> None:
    if not filters_report or not filters_report.get("enabled"):
        return

    enabled_filters = filters_report.get("filters") or {}

    if not enabled_filters:
        return

    lines.append("## Filters")
    lines.append("")

    contract_version = filters_report.get("contract_version")
    if contract_version:
        lines.append(f"- **Contract version:** `{contract_version}`")

    media = enabled_filters.get("media") or {}
    documents = enabled_filters.get("documents") or {}

    if media.get("enabled"):
        criteria = _active_criteria(media.get("criteria") or {})

        if criteria:
            lines.append(
                "- **Media criteria:** "
                + ", ".join(f"`{key}={value}`" for key, value in criteria.items())
            )

        media_items = media.get("items") or []
        media_count = int(media.get("match_count") or len(media_items))

        lines.append(f"- **Filtered media items:** {media_count}")

    if documents.get("enabled"):
        criteria = _active_criteria(documents.get("criteria") or {})

        if criteria:
            lines.append(
                "- **Document criteria:** "
                + ", ".join(f"`{key}={value}`" for key, value in criteria.items())
            )

        document_items = documents.get("items") or []
        document_count = int(documents.get("match_count") or len(document_items))

        lines.append(f"- **Filtered documents:** {document_count}")

    lines.append("")


def _append_find_summary(
    *,
    lines: list[str],
    find_report: dict[str, Any],
    fallback_find: str | None = None,
    fallback_matches: list[dict[str, Any]] | None = None,
) -> None:
    fallback_matches = fallback_matches or []

    query = (
        find_report.get("query")
        or find_report.get("find")
        or find_report.get("search")
        or fallback_find
    )

    matches = find_report.get("matches") or fallback_matches
    match_count = int(
        find_report.get("match_count")
        or find_report.get("matches_count")
        or len(matches)
    )

    if not query and match_count <= 0:
        return

    lines.append("## Find")
    lines.append("")

    contract_version = find_report.get("contract_version")
    if contract_version:
        lines.append(f"- **Contract version:** `{contract_version}`")

    if query:
        lines.append(f"- **Query:** `{query}`")

    lines.append(f"- **Matches:** {match_count}")

    case_sensitive = find_report.get("case_sensitive")
    if case_sensitive is not None:
        lines.append(f"- **Case sensitive:** `{case_sensitive}`")

    find_scope = find_report.get("find_scope")
    if find_scope:
        lines.append(f"- **Scope:** `{find_scope}`")

    searched_report = find_report.get("find_report")
    if searched_report:
        lines.append(f"- **Report:** `{searched_report}`")

    field_counts = (
        find_report.get("field_counts")
        or find_report.get("matches_by_field")
        or _count_matches_by_key(matches, "field")
    )

    if field_counts:
        lines.append(
            "- **Matches by field:** "
            + ", ".join(
                f"`{field}`: {count}"
                for field, count in sorted(field_counts.items())
            )
        )

    page_counts = (
        find_report.get("page_counts")
        or find_report.get("matches_by_page")
        or _count_matches_by_page(matches)
    )

    if page_counts:
        lines.append(
            "- **Matches by page:** "
            + ", ".join(
                f"`{page}`: {count}"
                for page, count in sorted(page_counts.items())
            )
        )

    lines.append("")


def _append_report_metadata(
    *,
    lines: list[str],
    metadata: dict[str, Any],
    requested_reports: list[str],
    limits: dict[str, Any],
    flags: dict[str, Any],
    evidence: dict[str, Any],
    coverage: dict[str, Any],
) -> None:
    if not metadata:
        return

    lines.append("## Report Metadata")
    lines.append("")

    contract_version = metadata.get("contract_version")
    if contract_version:
        lines.append(f"- **Contract version:** `{contract_version}`")

    if requested_reports:
        lines.append(
            "- **Requested reports:** "
            + ", ".join(f"`{item}`" for item in requested_reports)
        )

        if evidence:
            origin = evidence.get("origin")

            if origin:
                lines.append(
                    "- **Evidence source:** "
                    f"{_evidence_origin_label(origin)}"
                )

            reused = evidence.get("reused")
            if reused is not None:
                lines.append(
                    f"- **Evidence reused:** `{bool(reused)}`"
                )

            snapshot_created_at = evidence.get(
                "snapshot_created_at"
            )

            if snapshot_created_at:
                lines.append(
                    "- **Evidence acquired:** "
                    f"`{snapshot_created_at}`"
                )

            snapshot_age_seconds = evidence.get(
                "snapshot_age_seconds"
            )

            if snapshot_age_seconds is not None:
                lines.append(
                    "- **Evidence age:** "
                    f"{_format_duration(snapshot_age_seconds)}"
                )

            snapshot_id = evidence.get("snapshot_id")

            if snapshot_id:
                lines.append(
                    f"- **Snapshot ID:** `{snapshot_id}`"
                )
        if coverage.get("checked"):
            lines.append(
                "- **Coverage complete:** "
                f"`{bool(coverage.get('complete', False))}`"
            )

            lines.append(
                "- **Reusable pages:** "
                f"{coverage.get('reusable_count', 0)}"
            )

            lines.append(
                "- **Acquisition candidates:** "
                f"{coverage.get('acquisition_candidates', 0)}"
            )

            lines.append(
                "- **Planned acquisition:** "
                f"{coverage.get('planned_acquisition_count', 0)}"
            )

            if coverage.get("plan_truncated"):
                lines.append(
                    "- **Crawl plan truncated:** `True`"
                )

            reasons = coverage.get("reasons") or []

            if reasons:
                lines.append(
                    "- **Coverage reasons:** "
                    + ", ".join(
                        f"`{reason}`"
                        for reason in reasons
                    )
                )

    filters = metadata.get("filters") or {}
    if filters:
        active = {
            key: value
            for key, value in filters.items()
            if value not in (None, "", [])
        }

        if active:
            lines.append(
                "- **Filters:** "
                + ", ".join(f"`{key}={value}`" for key, value in active.items())
            )

    if limits:
        max_pages = limits.get("max_pages")
        max_depth = limits.get("max_depth")
        timeout_seconds = limits.get("timeout_seconds")
        max_chars_per_page = limits.get("max_chars_per_page")

        limit_parts: list[str] = []

        if max_pages is not None:
            limit_parts.append(f"max pages `{max_pages}`")

        if max_depth is not None:
            limit_parts.append(f"max depth `{max_depth}`")

        if timeout_seconds is not None:
            limit_parts.append(f"timeout `{timeout_seconds}s`")

        if max_chars_per_page is not None:
            limit_parts.append(f"max chars/page `{max_chars_per_page}`")

        if limit_parts:
            lines.append("- **Limits:** " + ", ".join(limit_parts))

    if flags:
        flag_parts = [
            f"`same_domain_only={flags.get('same_domain_only')}`",
            f"`include_links={flags.get('include_links')}`",
            f"`include_raw_pages={flags.get('include_raw_pages')}`",
            f"`include_security_audit={flags.get('include_security_audit')}`",
            f"`include_errors={flags.get('include_errors')}`",
        ]

        lines.append("- **Flags:** " + ", ".join(flag_parts))

    lines.append("")


def _append_crawl_summary(
    *,
    lines: list[str],
    page_totals: dict[str, Any],
    content_totals: dict[str, Any],
    html_block_totals: dict[str, Any],
    link_totals: dict[str, Any],
    document_totals: dict[str, Any],
    media_totals: dict[str, Any],
    form_totals: dict[str, Any],
    seo_totals: dict[str, Any],
    error_totals: dict[str, Any],
    coverage: dict[str, Any],
) -> None:
    lines.append("## Crawl Summary")
    lines.append("")

    if coverage.get("checked"):
        lines.append(
            "- **Coverage:** "
            f"{'complete' if coverage.get('complete') else 'partial'}"
        )

        lines.append(
            "- **Reusable pages:** "
            f"{coverage.get('reused_pages', 0)}"
        )

        lines.append(
            "- **Acquisition candidates:** "
            f"{coverage.get('acquisition_candidates', 0)}"
        )

        lines.append(
            "- **Planned acquisition:** "
            f"{coverage.get('planned_acquisition', 0)}"
        )

    if page_totals:
        lines.append(f"- **Successful pages:** {page_totals.get('successful_pages', 0)}")
        lines.append(f"- **Failed pages:** {page_totals.get('failed_pages', 0)}")

    if content_totals:
        lines.append(f"- **Content blocks:** {content_totals.get('content_blocks', 0)}")
        lines.append(f"- **Primary evidence blocks:** {content_totals.get('primary_evidence_blocks', 0)}")
        lines.append(f"- **Total extracted text length:** {content_totals.get('total_text_len', 0)}")

    if html_block_totals:
        lines.append(f"- **Detected HTML blocks:** {html_block_totals.get('detected_html_blocks', 0)}")

    if link_totals:
        lines.append(f"- **Unique links:** {link_totals.get('unique_links', 0)}")
        lines.append(f"- **External links:** {link_totals.get('external_links', 0)}")
        lines.append(f"- **Download links:** {link_totals.get('download_links', 0)}")
        lines.append(f"- **Social links:** {link_totals.get('social_links', 0)}")

    if document_totals:
        lines.append(f"- **Documents:** {document_totals.get('documents', 0)}")
        lines.append(f"- **PDFs:** {document_totals.get('pdfs', 0)}")

    if media_totals:
        lines.append(f"- **Media items:** {media_totals.get('total_media', 0)}")
        lines.append(f"- **Unique media:** {media_totals.get('unique_media', 0)}")

    if form_totals:
        lines.append(f"- **Forms:** {form_totals.get('forms', 0)}")
        lines.append(f"- **Sensitive controls:** {form_totals.get('sensitive_controls', 0)}")

    if seo_totals:
        average_score = float(seo_totals.get("average_score") or 0)
        page_count = int(seo_totals.get("page_count") or 0)
        grades = seo_totals.get("grades") or {}

        score_scale = seo_totals.get("score_scale") or {}
        score_max = float(score_scale.get("max") or 1.0)
        percentage_equivalent = bool(
            score_scale.get("percentage_equivalent", True)
        )

        score_percent = (
            round((average_score / score_max) * 100, 1)
            if score_max > 0
            else 0
        )

        if percentage_equivalent:
            lines.append(
                f"- **SEO average score:** "
                f"{average_score:.2f} / {score_max:.2f} ({score_percent}%)"
            )
        else:
            lines.append(
                f"- **SEO average score:** {average_score:.2f} / {score_max:.2f}"
            )

        if grades:
            grade_parts = [
                f"{grade}: {count}/{page_count} pages"
                for grade, count in sorted(grades.items())
            ]

            lines.append("- **SEO grades:** " + ", ".join(grade_parts))

    if error_totals:
        lines.append(f"- **Security audit entries:** {error_totals.get('security_audit_entries', 0)}")
        lines.append(f"- **Blocked security events:** {error_totals.get('blocked_security_events', 0)}")

    lines.append("")


def _append_pages_summary(
    *,
    lines: list[str],
    pages: list[dict[str, Any]],
) -> None:
    if not pages:
        return

    lines.append("## Pages")
    lines.append("")

    for index, page in enumerate(pages, start=1):
        title = page.get("title") or "Untitled page"
        url = page.get("final_url") or page.get("url") or ""
        status_code = page.get("status_code") or "unknown"
        error = page.get("error")

        line = f"{index}. **{title}** — `{status_code}` — {url}"

        if error:
            line += f" — error: `{error}`"

        lines.append(line)

    lines.append("")


def _append_available_reports(
    *,
    lines: list[str],
    reports: dict[str, Any],
) -> None:
    available_reports = sorted(reports.keys())

    if not available_reports:
        return

    lines.append("## Available Reports")
    lines.append("")
    lines.append(", ".join(f"`{key}`" for key in available_reports))
    lines.append("")


def _append_matches(
    *,
    lines: list[str],
    matches: list[dict[str, Any]],
) -> None:
    lines.append("## Matches")
    lines.append("")

    for index, match in enumerate(matches[:20], start=1):
        page = match.get("page_title") or match.get("page_url") or "Unknown page"
        field = match.get("field") or "text"
        match_text = match.get("match_text") or ""
        before = match.get("before") or ""
        after = match.get("after") or ""

        lines.append(f"### Match {index}")
        lines.append(f"- **Page:** {page}")
        lines.append(f"- **Field:** {field}")
        lines.append("")
        lines.append("```text")
        lines.append(f"{before} [{match_text}] {after}".strip())
        lines.append("```")
        lines.append("")

    if len(matches) > 20:
        lines.append(f"_Showing first 20 of {len(matches)} matches._")
        lines.append("")


def _append_errors_and_security(
    *,
    lines: list[str],
    errors: list[str],
    security_audit: list[dict[str, Any]],
) -> None:
    lines.append("## Errors / Security")
    lines.append("")

    if errors:
        lines.append("### Crawl Errors")
        lines.append("")
        for error in errors[:20]:
            lines.append(f"- {error}")
        if len(errors) > 20:
            lines.append(f"- ...and {len(errors) - 20} more errors.")
        lines.append("")

    if security_audit:
        decisions: dict[str, int] = {}
        categories: dict[str, int] = {}

        for entry in security_audit:
            decision = str(entry.get("decision") or "unknown")
            category = str(entry.get("category") or "unknown")

            decisions[decision] = decisions.get(decision, 0) + 1
            categories[category] = categories.get(category, 0) + 1

        lines.append("### Security Audit Summary")
        lines.append("")
        lines.append(f"- **Entries:** {len(security_audit)}")
        lines.append(f"- **Decisions:** {decisions}")
        lines.append(f"- **Categories:** {categories}")
        lines.append("")

        notable = [
            entry for entry in security_audit
            if str(entry.get("decision") or "").lower() in {"block", "blocked", "skip", "skipped", "error"}
        ]

        if notable:
            lines.append("### Notable Security Entries")
            lines.append("")

            for entry in notable[:20]:
                lines.append(
                    f"- **{str(entry.get('decision') or '').upper()}** "
                    f"`{entry.get('stage')}` "
                    f"{entry.get('url')}: {entry.get('reason')}"
                )

            lines.append("")


def _active_criteria(criteria: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in criteria.items()
        if value not in (None, "", [])
    }


def _count_matches_by_key(
    matches: list[dict[str, Any]],
    key: str,
) -> dict[str, int]:
    counts: dict[str, int] = {}

    for match in matches:
        value = str(match.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1

    return counts


def _count_matches_by_page(
    matches: list[dict[str, Any]],
) -> dict[str, int]:
    counts: dict[str, int] = {}

    for match in matches:
        page = (
            match.get("page_title")
            or match.get("page_url")
            or "unknown"
        )
        page = str(page)

        counts[page] = counts.get(page, 0) + 1

    return counts

def _evidence_origin_label(
    origin: Any,
) -> str:
    normalized = str(
        origin or "unknown"
    ).strip().lower()

    labels = {
        "network": "Live crawl",
        "snapshot": "Reused session snapshot",
        "incremental": "Incremental crawl",
        "unknown": "Unknown",
    }

    return labels.get(
        normalized,
        normalized.replace("_", " ").title(),
    )


def _format_duration(
    seconds: Any,
) -> str:
    try:
        total_seconds = max(
            0,
            int(float(seconds)),
        )
    except (TypeError, ValueError):
        return "unknown"

    if total_seconds < 60:
        return f"{total_seconds}s"

    minutes, seconds = divmod(
        total_seconds,
        60,
    )

    if minutes < 60:
        if seconds:
            return f"{minutes}m {seconds}s"

        return f"{minutes}m"

    hours, minutes = divmod(
        minutes,
        60,
    )

    if minutes:
        return f"{hours}h {minutes}m"

    return f"{hours}h"