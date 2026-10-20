from __future__ import annotations

from typing import Any

from src.base.services.file_service import (
    FileMissingError,
    FileOwnershipError,
    FileService,
    mime_accepted,
)
from src.domains.agentverse.tools.base import ToolExecutionError
from src.domains.agentverse.workflows.spec import WorkflowInputField


async def materialize_file_inputs(
    *,
    inputs: dict[str, Any],
    declared: list[WorkflowInputField],
    file_service: FileService | None,
    user_id: str | None,
) -> dict[str, Any]:
    """Validate declared ``file`` inputs and swap their ``file_id`` strings for
    resolved file dicts ``{file_id, user_id, name, mime, size}``.

    Runs once at ``op=start``; the materialized dicts persist in ``run.inputs``,
    so resume and step-time binding resolution never need the catalog spec or
    storage access again. SAS URLs are minted downstream by the consuming tool -
    the same path chat-attached files take.

    Raises ``ToolExecutionError`` on any violation: a run must not start with an
    unusable file input.
    """
    file_fields = [f for f in declared if f.is_file]
    if not file_fields:
        return inputs

    materialized = dict(inputs)

    for field in file_fields:
        value = materialized.get(field.key)

        if value is None or value == "":
            if field.required:
                raise ToolExecutionError(f"Required file input '{field.key}' is missing")
            # Drop empty optional file inputs so `${inputs.<key>}` references
            # fail with a clear missing-value error instead of resolving to "".
            materialized.pop(field.key, None)
            continue

        if not isinstance(value, str):
            raise ToolExecutionError(f"File input '{field.key}' must be a single file_id string")

        if file_service is None:
            raise ToolExecutionError("File storage is not configured on this server")

        if not user_id:
            raise ToolExecutionError(f"File input '{field.key}' requires an authenticated user")

        try:
            meta = await file_service.get_metadata(value, requesting_user_id=user_id)
        except FileOwnershipError:
            raise ToolExecutionError(
                f"File input '{field.key}': file '{value}' does not belong to this user"
            )
        except FileMissingError:
            raise ToolExecutionError(f"File input '{field.key}': file '{value}' was not found")

        if field.accepted_mimes and not mime_accepted(meta.mime, field.accepted_mimes):
            raise ToolExecutionError(
                f"File input '{field.key}': mime '{meta.mime}' not accepted. "
                f"Allowed: {field.accepted_mimes}"
            )

        materialized[field.key] = {
            "file_id": meta.file_id,
            "user_id": user_id,
            "name": meta.name,
            "mime": meta.mime,
            "size": meta.size,
        }

    return materialized
