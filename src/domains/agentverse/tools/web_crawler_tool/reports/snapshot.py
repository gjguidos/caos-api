from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from src.domains.agentverse.tools.web_crawler_tool.snapshots.models import (
    PageSnapshot,
    SnapshotChangeSummary,
    SnapshotReport,
)

SNAPSHOT_VERSION = "1.0"


def build_snapshot_report(
    *,
    page: dict[str, Any],
    previous: dict[str, Any] | None = None,
) -> SnapshotReport:
    current = build_page_snapshot(page=page)

    previous_snapshot = None
    if previous:
        previous_snapshot = (
            PageSnapshot(**previous)
            if not isinstance(previous, PageSnapshot)
            else previous
        )

    changed_fields: list[str] = []

    if previous_snapshot:
        if current.content_hash != previous_snapshot.content_hash:
            changed_fields.append("content")

        if current.structure_hash != previous_snapshot.structure_hash:
            changed_fields.append("structure")

        if current.link_hash != previous_snapshot.link_hash:
            changed_fields.append("links")

        if current.metadata_hash != previous_snapshot.metadata_hash:
            changed_fields.append("metadata")

        if current.form_hash != previous_snapshot.form_hash:
            changed_fields.append("forms")

        if current.component_hash != previous_snapshot.component_hash:
            changed_fields.append("components")

    return SnapshotReport(
        current=current,
        previous=previous_snapshot,
        changed=bool(changed_fields),
        changed_fields=changed_fields,
        change_summary=SnapshotChangeSummary(
            content_changed="content" in changed_fields,
            structure_changed="structure" in changed_fields,
            links_changed="links" in changed_fields,
            metadata_changed="metadata" in changed_fields,
            forms_changed="forms" in changed_fields,
            components_changed="components" in changed_fields,
        ),
    )


def build_page_snapshot(*, page: dict[str, Any]) -> PageSnapshot:
    html_report = page.get("html_report") or {}
    reports = page.get("reports") or {}

    normalized_text = normalize_text(page.get("text") or "")
    normalized_title = normalize_text(page.get("title") or "")

    links_payload = normalize_links(page.get("links") or [])
    structure_payload = build_structure_payload(html_report)
    metadata_payload = build_metadata_payload(page=page, html_report=html_report)
    forms_payload = build_forms_payload(reports)
    components_payload = build_components_payload(reports)

    return PageSnapshot(
        url=page.get("url") or "",
        final_url=page.get("final_url"),
        title=page.get("title") or "",
        content_hash=stable_hash(
            {
                "title": normalized_title,
                "text": normalized_text,
            }
        ),
        structure_hash=stable_hash(structure_payload),
        link_hash=stable_hash(links_payload),
        metadata_hash=stable_hash(metadata_payload),
        form_hash=stable_hash(forms_payload),
        component_hash=stable_hash(components_payload),
        snapshot_version=SNAPSHOT_VERSION,
        metadata={
            "text_len": len(normalized_text),
            "link_count": len(links_payload),
            "heading_count": len((html_report.get("headings") or [])),
            "status_code": page.get("status_code"),
            "content_type": page.get("content_type"),
        },
    )


def build_structure_payload(html_report: dict[str, Any]) -> dict[str, Any]:
    headings = html_report.get("headings") or []
    semantic_regions = html_report.get("semantic_regions") or {}
    interactive = html_report.get("interactive") or {}
    media = html_report.get("media") or {}

    return {
        "semantic_regions": semantic_regions,
        "headings": [
            {
                "level": heading.get("level"),
                "text": normalize_text(heading.get("text") or ""),
            }
            for heading in headings
            if isinstance(heading, dict)
        ],
        "interactive_counts": {
            "links": interactive.get("link_count", 0),
            "buttons": interactive.get("button_count", 0),
            "roles": interactive.get("role_counts", {}),
        },
        "media_counts": {
            "images": media.get("image_count", 0),
            "videos": media.get("video_count", 0),
            "iframes": media.get("iframe_count", 0),
        },
    }


def build_metadata_payload(
    *,
    page: dict[str, Any],
    html_report: dict[str, Any],
) -> dict[str, Any]:
    seo = html_report.get("seo") or {}
    page_meta = html_report.get("page") or {}

    return {
        "title": normalize_text(page.get("title") or page_meta.get("title") or ""),
        "description": normalize_text(seo.get("description") or ""),
        "canonical": seo.get("canonical"),
        "open_graph": seo.get("open_graph") or {},
        "twitter": seo.get("twitter") or {},
        "json_ld_count": seo.get("json_ld_count", 0),
        "lang": page_meta.get("lang"),
    }


def build_forms_payload(reports: dict[str, Any]) -> dict[str, Any]:
    forms = reports.get("forms") or {}

    return {
        "form_count": forms.get("form_count", 0),
        "total_controls": forms.get("total_controls", 0),
        "forms": [
            {
                "method": form.get("method"),
                "action_url": form.get("action_url"),
                "form_role": form.get("form_role"),
                "control_count": form.get("control_count"),
                "controls": [
                    {
                        "tag": control.get("tag"),
                        "type": control.get("type"),
                        "name": control.get("name"),
                        "control_role": control.get("control_role"),
                        "required": control.get("required"),
                    }
                    for control in (form.get("controls") or [])
                    if isinstance(control, dict)
                ],
            }
            for form in (forms.get("forms") or [])
            if isinstance(form, dict)
        ],
    }


def build_components_payload(reports: dict[str, Any]) -> dict[str, Any]:
    content_blocks = reports.get("content_blocks") or {}

    return {
        "block_count": content_blocks.get("block_count", 0),
        "hero_blocks": content_blocks.get("hero_blocks", 0),
        "cta_blocks": content_blocks.get("cta_blocks", 0),
        "card_blocks": content_blocks.get("card_blocks", 0),
        "article_blocks": content_blocks.get("article_blocks", 0),
        "blocks": [
            {
                "block_type": block.get("block_type"),
                "heading": normalize_text(block.get("heading") or ""),
                "text_len": block.get("text_len", 0),
                "link_count": block.get("link_count", 0),
                "media_count": block.get("media_count", 0),
                "signals": block.get("signals") or [],
            }
            for block in (content_blocks.get("blocks") or [])
            if isinstance(block, dict)
        ],
    }


def normalize_links(links: list[str]) -> list[str]:
    cleaned = []

    for link in links:
        if not isinstance(link, str):
            continue

        value = link.strip()

        if not value:
            continue

        cleaned.append(value.rstrip("/"))

    return sorted(set(cleaned))


def normalize_text(value: str) -> str:
    value = value or ""
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def stable_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()