from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)

LOCAL_ARTIFACT_ROOT = Path(
    os.getenv(
        "AGENTVERSE_LOCAL_ARTIFACT_ROOT",
        "/app/data/artifacts",
    )
)

CRAWLER_ARTIFACT_REPORTS = (
    "crawl_summary",
    "seo",
    "links",
    "documents",
    "media",
    "content_blocks",
    "detected_html_blocks",
    "find",
    "filters",
    "crawl_graph",
    "forms",
)


TEMPORARY_CRAWLER_ARTIFACT_CONTRACT: dict[str, Any] = {
    "schema": "artifact.builder.contract.v1",
    "metadata": {
        "artifact_type": "crawl_report",
        "producer": "web_crawler",
        "operation": "crawl",
    },
    "artifacts": [
        {
            "id": report,
            "source": f"reports.{report}",
            "format": "json",
            "filename": f"{report}.json",
            "required": report == "crawl_summary",
            "skip_if_missing": report != "crawl_summary",
            "skip_if_empty": False,
            "metadata": {
                "report": report,
            },
        }
        for report in CRAWLER_ARTIFACT_REPORTS
    ],
}


async def persist_crawler_artifacts(
    *,
    result_payload: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    TEMPORARY local implementation of artifact.builder.contract.v1.

    Persists crawler artifacts to local disk while the workflow-backed
    artifact builder and uploader are unavailable.
    """
    run_id = uuid4().hex
    run_dir = LOCAL_ARTIFACT_ROOT / "web_crawler" / run_id

    await asyncio.to_thread(
        run_dir.mkdir,
        parents=True,
        exist_ok=True,
    )

    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        "[🕷️ WEB CRAWLER][LOCAL ARTIFACT][CREATE]",
        extra={
            "run_id": run_id,
            "run_dir": str(run_dir),
        },
    )

    contract = TEMPORARY_CRAWLER_ARTIFACT_CONTRACT

    contract_metadata = {
        **contract.get("metadata", {}),
        **(metadata or {}),
        "run_id": run_id,
    }

    artifacts: list[dict[str, Any]] = []

    for definition in contract["artifacts"]:
        artifact = await asyncio.to_thread(
            _build_local_artifact,
            definition=definition,
            result_payload=result_payload,
            run_dir=run_dir,
            contract_metadata=contract_metadata,
        )

        if artifact is not None:
            artifacts.append(artifact)

    await _write_manifest(
        run_dir=run_dir,
        run_id=run_id,
        metadata=contract_metadata,
        artifacts=artifacts,
    )

    return artifacts


def _build_local_artifact(
    *,
    definition: dict[str, Any],
    result_payload: dict[str, Any],
    run_dir: Path,
    contract_metadata: dict[str, Any],
) -> dict[str, Any] | None:
    artifact_id = definition["id"]
    source = definition["source"]

    content = _resolve_source(
        result_payload,
        source,
    )

    missing = content is None
    empty = _is_empty(content)

    if missing:
        if definition.get("skip_if_missing", False):
            return None

        if definition.get("required", False):
            raise ValueError(
                f"Required artifact source missing: {source}"
            )

    if empty and definition.get("skip_if_empty", False):
        return None

    artifact_metadata = {
        **contract_metadata,
        **definition.get("metadata", {}),
    }

    artifact_format = definition.get("format", "json")
    filename = definition["filename"]
    path = run_dir / filename

    if artifact_format != "json":
        raise ValueError(
            "Temporary local artifact sink does not support "
            f"format '{artifact_format}' for '{artifact_id}'."
        )

    serialized = json.dumps(
        content,
        indent=2,
        ensure_ascii=False,
        default=str,
    )

    encoded = serialized.encode("utf-8")
    path.write_bytes(encoded)

    return {
        "id": artifact_id,
        "source": source,
        "format": artifact_format,
        "filename": filename,
        "media_type": "application/json",
        "size_bytes": len(encoded),
        "metadata": artifact_metadata,
        "storage": {
            "provider": "local",
            "path": str(path),
        },
    }

def _write_manifest_sync(
    *,
    path: Path,
    manifest: dict[str, Any],
) -> None:
    serialized = json.dumps(
        manifest,
        indent=2,
        ensure_ascii=False,
        default=str,
    )

    path.write_bytes(
        serialized.encode("utf-8")
    )


async def _write_manifest(
    *,
    run_dir: Path,
    run_id: str,
    metadata: dict[str, Any],
    artifacts: list[dict[str, Any]],
) -> None:
    """
    Writes run-level metadata without changing the contents of the
    individual crawler report files.
    """
    manifest = {
        "schema": "artifact.manifest.v1",
        "run_id": run_id,
        "metadata": metadata,
        "artifacts": [
            {
                "id": artifact["id"],
                "source": artifact["source"],
                "format": artifact["format"],
                "filename": artifact["filename"],
                "media_type": artifact["media_type"],
                "size_bytes": artifact["size_bytes"],
                "metadata": artifact["metadata"],
                "storage": artifact["storage"],
            }
            for artifact in artifacts
        ],
    }

    await asyncio.to_thread(
        _write_manifest_sync,
        path=run_dir / "metadata.json",
        manifest=manifest,
    )


    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        "[🕷️ WEB CRAWLER][ARTIFACT MANIFEST STORED] ",
        extra={
            "run_id": run_id,
            "artifact_count": len(artifacts),
        },
    )


def _resolve_source(
    payload: dict[str, Any],
    source: str,
) -> Any:
    current: Any = payload

    for segment in source.split("."):
        if not isinstance(current, dict):
            return None

        current = current.get(segment)

        if current is None:
            return None

    return current


def _is_empty(value: Any) -> bool:
    if value is None:
        return True

    if isinstance(value, str):
        return not value.strip()

    if isinstance(value, dict | list | tuple | set):
        return len(value) == 0

    return False