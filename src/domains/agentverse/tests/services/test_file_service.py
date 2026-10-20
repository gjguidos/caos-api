"""Unit tests for FileService."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.base.config.blob_storage_config import BlobStorageConfig
from src.base.services.file_service import (
    FileMissingError,
    FileOwnershipError,
    FileService,
    _blob_name,
    _sha256_hex,
)

# ------------------------------------------------------------------ helpers


def _make_service(
    conn_str: str = "DefaultEndpointsProtocol=https;AccountName=acct;AccountKey=key==;EndpointSuffix=core.windows.net",
) -> FileService:
    cfg = BlobStorageConfig()
    cfg.blob_storage_connection_string = conn_str
    cfg.blob_storage_account_name = "acct"
    cfg.blob_storage_account_key = "key=="
    return FileService(config=cfg)


# ------------------------------------------------------------------ unit tests


def test_blob_name_structure():
    assert _blob_name("default", "user1", "f_abc") == "default/user1/f_abc"


def test_sha256_hex_is_deterministic():
    data = b"hello"
    assert _sha256_hex(data) == _sha256_hex(data)
    assert len(_sha256_hex(data)) == 64


@pytest.mark.asyncio
async def test_upload_rejects_oversized_file():
    svc = _make_service()
    huge = b"x" * (201 * 1024 * 1024)
    with pytest.raises(ValueError, match="exceeds maximum"):
        await svc.upload(
            user_id="u1",
            target_tool="extract",
            filename="big.pptx",
            data=huge,
            accepted_mimes=None,
            max_size_bytes=200 * 1024 * 1024,
        )


@pytest.mark.asyncio
async def test_upload_rejects_wrong_mime():
    svc = _make_service()
    data = b"%PDF-1.4"

    with patch("src.base.services.file_service._sniff_mime", return_value="application/pdf"):
        with pytest.raises(ValueError, match="not accepted"):
            await svc.upload(
                user_id="u1",
                target_tool="extract",
                filename="file.pdf",
                data=data,
                accepted_mimes=[
                    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
                ],
                max_size_bytes=200 * 1024 * 1024,
            )


@pytest.mark.asyncio
async def test_upload_success():
    svc = _make_service()
    pptx_mime = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    data = b"PK\x03\x04fake_pptx_content"

    mock_blob_client = AsyncMock()
    mock_container_client = MagicMock()
    mock_container_client.get_blob_client.return_value = mock_blob_client
    mock_service_client = MagicMock()
    mock_service_client.get_blob_client.return_value = mock_blob_client

    with patch("src.base.services.file_service._sniff_mime", return_value=pptx_mime):
        with patch.object(svc, "_get_client", return_value=mock_service_client):
            result = await svc.upload(
                user_id="user1",
                target_tool="extract",
                filename="deck.pptx",
                data=data,
                accepted_mimes=[pptx_mime],
                max_size_bytes=200 * 1024 * 1024,
            )

    assert result.file_id.startswith("f_")
    assert result.name == "deck.pptx"
    assert result.mime == pptx_mime
    assert result.size == len(data)
    assert len(result.sha256) == 64
    assert result.user_id == "user1"
    assert result.target_tool == "extract"
    mock_blob_client.upload_blob.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_metadata_ownership_mismatch_raises():
    svc = _make_service()

    mock_props = MagicMock()
    mock_props.metadata = {
        "user_id": "other_user",
        "tenant_id": "default",
        "target_tool": "extract",
        "original_name": "x.pptx",
        "sha256": "ab12",
        "uploaded_at": "2026-01-01",
    }
    mock_props.content_settings.content_type = "application/octet-stream"
    mock_props.size = 100

    mock_blob_client = AsyncMock()
    mock_blob_client.get_blob_properties.return_value = mock_props
    mock_service_client = MagicMock()
    mock_service_client.get_blob_client.return_value = mock_blob_client

    with patch.object(svc, "_get_client", return_value=mock_service_client):
        with pytest.raises(FileOwnershipError):
            await svc.get_metadata("f_abc", requesting_user_id="user1")


@pytest.mark.asyncio
async def test_get_metadata_not_found_raises():
    from azure.core.exceptions import ResourceNotFoundError

    svc = _make_service()

    mock_blob_client = AsyncMock()
    mock_blob_client.get_blob_properties.side_effect = ResourceNotFoundError("not found")
    mock_service_client = MagicMock()
    mock_service_client.get_blob_client.return_value = mock_blob_client

    with patch.object(svc, "_get_client", return_value=mock_service_client):
        with pytest.raises(FileMissingError):
            await svc.get_metadata("f_abc", requesting_user_id="user1")


@pytest.mark.asyncio
async def test_delete_many_is_best_effort():
    svc = _make_service()

    mock_blob_client = AsyncMock()
    mock_blob_client.delete_blob.side_effect = Exception("network error")
    mock_service_client = MagicMock()
    mock_service_client.get_blob_client.return_value = mock_blob_client

    with patch.object(svc, "_get_client", return_value=mock_service_client):
        await svc.delete_many(["f_1", "f_2"], user_id="user1")


@pytest.mark.asyncio
async def test_delete_many_empty_list_is_noop():
    svc = _make_service()
    with patch.object(svc, "_get_client") as mock_get:
        await svc.delete_many([], user_id="user1")
        mock_get.assert_not_called()
