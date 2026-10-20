"""File upload / management service backed by Azure Blob Storage."""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import puremagic
from pydantic import BaseModel

from src.base.config.blob_storage_config import BlobStorageConfig

logger = logging.getLogger(__name__)

GLOBAL_MAX_BYTES = 200 * 1024 * 1024  # 200 MB hard ceiling


class FileMetadata(BaseModel):
    file_id: str
    name: str
    mime: str
    size: int
    sha256: str
    user_id: str = ""
    tenant_id: str = "default"
    target_tool: str = ""
    target_workflow: str = ""
    uploaded_at: str = ""


class FileOwnershipError(Exception):
    """Raised when the requesting user does not own the file."""


class FileMissingError(Exception):
    """Raised when a file_id cannot be located in blob storage."""


def _generate_file_id() -> str:
    return f"f_{uuid.uuid4().hex}"


def _sniff_mime(data: bytes) -> str:
    try:
        matches = puremagic.magic_string(data)
    except puremagic.PureError:
        return "application/octet-stream"
    if not matches:
        return "application/octet-stream"
    return matches[0].mime_type or "application/octet-stream"


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def mime_accepted(mime: str, accepted: list[str] | None) -> bool:
    """Whether ``mime`` matches ``accepted``. Patterns are exact matches or
    ``type/*`` wildcards. Comparison is case-insensitive and whitespace-trimmed.

    Both ``None`` and an empty list mean "no restriction, accept any mime" -
    callers that want to accept nothing must not call this. Tool and workflow
    constraints normalize an empty list to ``None`` before reaching here.
    """
    if not accepted:
        return True

    mime_lower = mime.lower()
    for pattern in accepted:
        pattern_lower = pattern.strip().lower()
        if pattern_lower == "*/*" or pattern_lower == mime_lower:
            return True
        if (
            pattern_lower.endswith("/*")
            and mime_lower.split("/", 1)[0] == pattern_lower[: -len("/*")]
        ):
            return True

    return False


def validate_upload(
    data: bytes,
    *,
    accepted_mimes: list[str] | None,
    max_size_bytes: int,
) -> str:
    """Shared size + mime validation for uploads. Returns the sniffed mime.

    Raises ``ValueError`` on violation (translated to 422 at the route layer).
    """
    effective_max = min(max_size_bytes, GLOBAL_MAX_BYTES)
    if len(data) > effective_max:
        raise ValueError(f"File size {len(data)} exceeds maximum {effective_max} bytes")

    sniffed_mime = _sniff_mime(data)
    if not mime_accepted(sniffed_mime, accepted_mimes):
        raise ValueError(f"MIME type '{sniffed_mime}' not accepted. Allowed: {accepted_mimes}")

    return sniffed_mime


def _blob_name(tenant_id: str, user_id: str, file_id: str) -> str:
    return f"{tenant_id}/{user_id}/{file_id}"


def _is_not_found(exc: Exception) -> bool:
    try:
        from azure.core.exceptions import ResourceNotFoundError  # type: ignore[import-untyped]

        return isinstance(exc, ResourceNotFoundError)
    except ImportError:
        return "404" in str(exc) or "not found" in str(exc).lower()


class FileService:
    """
    Manages file lifecycle: upload to Azure Blob Storage, SAS URL minting, and deletion.

    Blob path: {container}/{tenant_id}/{user_id}/{file_id}
    Blob metadata: user_id, tenant_id, target_tool, original_name, sha256, uploaded_at
    """

    def __init__(self, config: BlobStorageConfig) -> None:
        self._config = config
        self._client: Any = None

    # ------------------------------------------------------------------ internal

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client

        cfg = self._config
        try:
            from azure.storage.blob.aio import BlobServiceClient  # type: ignore[import-untyped]
        except ImportError:
            raise RuntimeError("azure-storage-blob is required: pip install azure-storage-blob")

        if cfg.blob_storage_connection_string:
            self._client = BlobServiceClient.from_connection_string(
                cfg.blob_storage_connection_string
            )
        elif cfg.blob_storage_account_url:
            if cfg.blob_storage_use_managed_identity:
                from azure.identity.aio import (
                    DefaultAzureCredential,  # type: ignore[import-untyped]
                )

                self._client = BlobServiceClient(
                    cfg.blob_storage_account_url,
                    credential=DefaultAzureCredential(),
                )
            else:
                self._client = BlobServiceClient(
                    cfg.blob_storage_account_url,
                    credential=cfg.blob_storage_account_key or None,
                )
        else:
            raise RuntimeError(
                "Blob storage not configured. Set BLOB_STORAGE_CONNECTION_STRING or "
                "BLOB_STORAGE_ACCOUNT_URL."
            )
        return self._client

    def _account_name(self) -> str:
        if self._config.blob_storage_account_name:
            return self._config.blob_storage_account_name
        if self._config.blob_storage_account_url:
            return self._config.blob_storage_account_url.split("//")[-1].split(".")[0]
        for part in (self._config.blob_storage_connection_string or "").split(";"):
            if part.startswith("AccountName="):
                return part.split("=", 1)[1]
        raise RuntimeError("Cannot determine storage account name from config")

    def _account_key(self) -> str:
        if self._config.blob_storage_account_key:
            return self._config.blob_storage_account_key
        for part in (self._config.blob_storage_connection_string or "").split(";"):
            if part.startswith("AccountKey="):
                return part.split("=", 1)[1]
        raise RuntimeError(
            "Account key required for SAS generation. "
            "Set BLOB_STORAGE_ACCOUNT_KEY or include it in the connection string."
        )

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
        blob = _blob_name(tenant_id, user_id, file_id)

        client = self._get_client()
        container = self._config.blob_storage_container_name
        blob_client = client.get_blob_client(container=container, blob=blob)

        # Empty values are dropped: Azure rejects empty metadata values.
        metadata = {
            k: v
            for k, v in {
                "user_id": user_id,
                "tenant_id": tenant_id,
                "target_tool": target_tool,
                "target_workflow": target_workflow,
                "original_name": filename,
                "sha256": sha,
                "uploaded_at": uploaded_at,
            }.items()
            if v
        }

        await blob_client.upload_blob(
            data,
            overwrite=True,
            content_type=sniffed_mime,
            metadata=metadata,
        )

        logger.info(
            "Uploaded file_id=%s name=%s size=%d sha256=%s target_tool=%s target_workflow=%s",
            file_id,
            filename,
            len(data),
            sha,
            target_tool,
            target_workflow,
        )

        return FileMetadata(
            file_id=file_id,
            name=filename,
            mime=sniffed_mime,
            size=len(data),
            sha256=sha,
            user_id=user_id,
            tenant_id=tenant_id,
            target_tool=target_tool,
            target_workflow=target_workflow,
            uploaded_at=uploaded_at,
        )

    async def get_metadata(self, file_id: str, requesting_user_id: str) -> FileMetadata:
        """
        Retrieve blob metadata for a file owned by requesting_user_id.

        Raises FileOwnershipError if the stored user_id differs.
        Raises FileMissingError if the blob does not exist.
        """
        client = self._get_client()
        container = self._config.blob_storage_container_name
        blob = _blob_name("default", requesting_user_id, file_id)
        blob_client = client.get_blob_client(container=container, blob=blob)

        try:
            props = await blob_client.get_blob_properties()
        except Exception as exc:
            if _is_not_found(exc):
                raise FileMissingError(file_id) from exc
            raise

        meta = props.metadata or {}
        stored_user_id = meta.get("user_id", "")

        if stored_user_id != requesting_user_id:
            raise FileOwnershipError(f"File '{file_id}' not owned by user '{requesting_user_id}'")

        return FileMetadata(
            file_id=file_id,
            name=meta.get("original_name", ""),
            mime=str(props.content_settings.content_type or ""),
            size=props.size or 0,
            sha256=meta.get("sha256", ""),
            user_id=stored_user_id,
            tenant_id=meta.get("tenant_id", "default"),
            target_tool=meta.get("target_tool", ""),
            target_workflow=meta.get("target_workflow", ""),
            uploaded_at=meta.get("uploaded_at", ""),
        )

    async def mint_sas_url(self, file_id: str, user_id: str, *, expiry_seconds: int = 300) -> str:
        """
        Generate a short-lived (default 5 min) read-only SAS URL.

        SECURITY: Never log the returned URL — it is a bearer credential.
        """
        from azure.storage.blob import (  # type: ignore[import-untyped]
            BlobSasPermissions,
            generate_blob_sas,
        )

        container = self._config.blob_storage_container_name
        blob = _blob_name("default", user_id, file_id)
        account_name = self._account_name()
        account_key = self._account_key()

        sas_token = generate_blob_sas(
            account_name=account_name,
            container_name=container,
            blob_name=blob,
            account_key=account_key,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.now(UTC) + timedelta(seconds=expiry_seconds),
        )

        logger.debug(
            "Minted SAS URL file_id=%s expiry_seconds=%d [url omitted]",
            file_id,
            expiry_seconds,
        )

        return f"https://{account_name}.blob.core.windows.net/{container}/{blob}?{sas_token}"

    async def delete_many(self, file_ids: list[str], *, user_id: str) -> None:
        """Best-effort deletion of multiple blobs. Never raises."""
        if not file_ids:
            return

        client = self._get_client()
        container = self._config.blob_storage_container_name

        for file_id in file_ids:
            blob = _blob_name("default", user_id, file_id)
            try:
                blob_client = client.get_blob_client(container=container, blob=blob)
                await blob_client.delete_blob(delete_snapshots="include")
                logger.debug("Deleted blob file_id=%s", file_id)
            except Exception as exc:
                if not _is_not_found(exc):
                    logger.warning("Failed to delete blob file_id=%s: %s", file_id, exc)
