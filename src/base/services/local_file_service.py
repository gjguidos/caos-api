"""Local filesystem FileService — for local development only. No Azure dependency."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime

from src.base.config.blob_storage_config import BlobStorageConfig
from src.base.services.file_service import (
    FileMetadata,
    FileMissingError,
    FileOwnershipError,
    FileService,
    _generate_file_id,
    _sha256_hex,
    validate_upload,
)

logger = logging.getLogger(__name__)


class LocalFileService(FileService):
    """
    Drop-in replacement for FileService that stores blobs on the local filesystem.

    Blob layout:
        {root}/{tenant_id}/{user_id}/{file_id}        — raw bytes
        {root}/{tenant_id}/{user_id}/{file_id}.meta   — JSON metadata sidecar

    mint_sas_url returns a short-lived URL pointing to the local dev serve endpoint
    (GET /api/v1/dev/files/{file_id}), which streams the bytes back to callers
    (e.g. the tool server) on the same Docker network.
    """

    def __init__(self, config: BlobStorageConfig) -> None:
        self._config = config
        self._client = None
        self._root = config.blob_storage_local_path
        os.makedirs(self._root, exist_ok=True)
        logger.info("LocalFileService initialised at root=%s", self._root)

    # ------------------------------------------------------------------ internal

    def _dir(self, tenant_id: str, user_id: str) -> str:
        path = os.path.join(self._root, tenant_id, user_id)
        os.makedirs(path, exist_ok=True)
        return path

    def _data_path(self, tenant_id: str, user_id: str, file_id: str) -> str:
        return os.path.join(self._dir(tenant_id, user_id), file_id)

    def _meta_path(self, tenant_id: str, user_id: str, file_id: str) -> str:
        return os.path.join(self._dir(tenant_id, user_id), f"{file_id}.meta")

    # ------------------------------------------------------------------ public API

    async def upload(
        self,
        *,
        user_id: str,
        tenant_id: str = "default",
        target_tool: str = "",
        target_workflow: str = "",
        filename: str,
        data: bytes,
        accepted_mimes: list[str] | None,
        max_size_bytes: int,
    ) -> FileMetadata:
        sniffed_mime = validate_upload(
            data, accepted_mimes=accepted_mimes, max_size_bytes=max_size_bytes
        )

        file_id = _generate_file_id()
        sha = _sha256_hex(data)
        uploaded_at = datetime.now(UTC).isoformat()

        with open(self._data_path(tenant_id, user_id, file_id), "wb") as fh:
            fh.write(data)

        meta_dict = {
            "file_id": file_id,
            "name": filename,
            "mime": sniffed_mime,
            "size": len(data),
            "sha256": sha,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "target_tool": target_tool,
            "target_workflow": target_workflow,
            "uploaded_at": uploaded_at,
        }
        with open(self._meta_path(tenant_id, user_id, file_id), "w") as fh:
            json.dump(meta_dict, fh)

        logger.info(
            "LocalFileService: uploaded file_id=%s name=%s size=%d sha256=%s target_tool=%s "
            "target_workflow=%s",
            file_id,
            filename,
            len(data),
            sha,
            target_tool,
            target_workflow,
        )
        return FileMetadata(**meta_dict)

    async def get_metadata(self, file_id: str, requesting_user_id: str) -> FileMetadata:
        meta_path = self._meta_path("default", requesting_user_id, file_id)
        if not os.path.exists(meta_path):
            raise FileMissingError(file_id)

        with open(meta_path) as fh:
            meta_dict = json.load(fh)

        if meta_dict.get("user_id") != requesting_user_id:
            raise FileOwnershipError(f"File '{file_id}' not owned by user '{requesting_user_id}'")

        return FileMetadata(**meta_dict)

    async def mint_sas_url(self, file_id: str, user_id: str, *, expiry_seconds: int = 300) -> str:
        exp = int(time.time()) + expiry_seconds
        base = self._config.blob_storage_local_base_url.rstrip("/")
        url = f"{base}/api/v1/dev/files/{file_id}?user_id={user_id}&exp={exp}"
        logger.debug(
            "LocalFileService: minted local URL file_id=%s expiry_seconds=%d [url omitted]",
            file_id,
            expiry_seconds,
        )
        return url

    async def delete_many(self, file_ids: list[str], *, user_id: str) -> None:
        if not file_ids:
            return
        for file_id in file_ids:
            for path in (
                self._data_path("default", user_id, file_id),
                self._meta_path("default", user_id, file_id),
            ):
                try:
                    os.remove(path)
                    logger.debug("LocalFileService: deleted file_id=%s", file_id)
                except FileNotFoundError:
                    pass
                except Exception as exc:
                    logger.warning(
                        "LocalFileService: failed to delete file_id=%s: %s", file_id, exc
                    )

    def read_bytes(self, file_id: str, user_id: str) -> tuple[bytes, str]:
        """Return (raw bytes, mime type). Called by the dev serve endpoint."""
        meta_path = self._meta_path("default", user_id, file_id)
        data_path = self._data_path("default", user_id, file_id)

        if not os.path.exists(meta_path) or not os.path.exists(data_path):
            raise FileMissingError(file_id)

        with open(meta_path) as fh:
            meta_dict = json.load(fh)
        with open(data_path, "rb") as fh:
            data = fh.read()

        return data, meta_dict.get("mime", "application/octet-stream")
