"""Unit tests for AgentChatService._validate_files."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.base.entities.user import User
from src.base.services.file_service import (
    FileMetadata,
    FileOwnershipError,
    FileService,
)
from src.domains.agentverse.services.agent_chat_service import AgentChatService

# ------------------------------------------------------------------ fixtures


def _make_user(user_id: str = "user1") -> User:
    return User(id=user_id, email="test@example.com", name="Test")


def _make_file_metadata(
    file_id: str = "f_abc",
    user_id: str = "user1",
    target_tool: str = "extract",
) -> FileMetadata:
    return FileMetadata(
        file_id=file_id,
        name="deck.pptx",
        mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        size=1000,
        sha256="deadbeef" * 8,
        user_id=user_id,
        tenant_id="default",
        target_tool=target_tool,
        uploaded_at="2026-01-01T00:00:00+00:00",
    )


def _make_service(file_service: FileService | None = None) -> AgentChatService:
    svc = AgentChatService.__new__(AgentChatService)
    svc.agent_service = MagicMock()
    svc.instance_registry = MagicMock()
    svc.file_service = file_service
    return svc


def _mock_registry_with_extract():
    meta = {
        "extract": {
            "supports": {
                "files": {
                    "max_count": 1,
                    "max_size_bytes": 209715200,
                    "accepted_mimes": [
                        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
                    ],
                }
            },
        }
    }
    return meta


# ------------------------------------------------------------------ tests


@pytest.mark.asyncio
async def test_validate_files_empty_returns_empty():
    svc = _make_service()
    result, err = await svc._validate_files(
        raw_files=[], text="@extract analyse", user=_make_user()
    )
    assert result == []
    assert err is None


@pytest.mark.asyncio
async def test_validate_files_no_tool_key_returns_error():
    svc = _make_service()

    with patch.object(svc.__class__, "_resolve_tool_key", return_value=None):
        result, err = await svc._validate_files(
            raw_files=[{"file_id": "f_abc"}],
            text="no tool key here",
            user=_make_user(),
        )

    assert result == []
    assert err is not None
    assert err["code"] == "NO_TARGET_TOOL"


@pytest.mark.asyncio
async def test_validate_files_tool_no_files_support_returns_error():
    svc = _make_service()

    with patch.object(
        svc.__class__,
        "_resolve_tool_key",
        return_value=("extract", {"supports": {}}),
    ):
        result, err = await svc._validate_files(
            raw_files=[{"file_id": "f_abc"}],
            text="@extract analyse",
            user=_make_user(),
        )

    assert result == []
    assert err is not None
    assert err["code"] == "TOOL_DOES_NOT_SUPPORT_FILES"


@pytest.mark.asyncio
async def test_validate_files_too_many_files_returns_error():
    svc = _make_service()

    with patch.object(
        svc.__class__,
        "_resolve_tool_key",
        return_value=(
            "extract",
            {"supports": {"files": {"max_count": 1}}},
        ),
    ):
        result, err = await svc._validate_files(
            raw_files=[{"file_id": "f_1"}, {"file_id": "f_2"}],
            text="@extract analyse",
            user=_make_user(),
        )

    assert result == []
    assert err is not None
    assert err["code"] == "TOO_MANY_FILES"


@pytest.mark.asyncio
async def test_validate_files_ownership_error_returns_error():
    mock_fs = AsyncMock(spec=FileService)
    mock_fs.get_metadata.side_effect = FileOwnershipError("not yours")
    svc = _make_service(file_service=mock_fs)

    with patch.object(
        svc.__class__,
        "_resolve_tool_key",
        return_value=(
            "extract",
            {"supports": {"files": {"max_count": 1}}},
        ),
    ):
        result, err = await svc._validate_files(
            raw_files=[{"file_id": "f_abc"}],
            text="@extract analyse",
            user=_make_user(),
        )

    assert result == []
    assert err is not None
    assert err["code"] == "FILE_NOT_OWNED"


@pytest.mark.asyncio
async def test_validate_files_target_mismatch_returns_error():
    file_meta = _make_file_metadata(target_tool="other_tool")
    mock_fs = AsyncMock(spec=FileService)
    mock_fs.get_metadata.return_value = file_meta
    svc = _make_service(file_service=mock_fs)

    with patch.object(
        svc.__class__,
        "_resolve_tool_key",
        return_value=(
            "extract",
            {"supports": {"files": {"max_count": 1}}},
        ),
    ):
        result, err = await svc._validate_files(
            raw_files=[{"file_id": "f_abc"}],
            text="@extract analyse",
            user=_make_user(),
        )

    assert result == []
    assert err is not None
    assert err["code"] == "FILE_TARGET_MISMATCH"


@pytest.mark.asyncio
async def test_validate_files_success():
    file_meta = _make_file_metadata()
    mock_fs = AsyncMock(spec=FileService)
    mock_fs.get_metadata.return_value = file_meta
    svc = _make_service(file_service=mock_fs)

    with patch.object(
        svc.__class__,
        "_resolve_tool_key",
        return_value=(
            "extract",
            {"supports": {"files": {"max_count": 1}}},
        ),
    ):
        result, err = await svc._validate_files(
            raw_files=[{"file_id": "f_abc"}],
            text="@extract analyse this deck",
            user=_make_user(),
        )

    assert err is None
    assert len(result) == 1
    assert result[0].file_id == "f_abc"


@pytest.mark.asyncio
async def test_validate_files_no_file_service_returns_error():
    svc = _make_service(file_service=None)

    with patch.object(
        svc.__class__,
        "_resolve_tool_key",
        return_value=(
            "extract",
            {"supports": {"files": {"max_count": 1}}},
        ),
    ):
        result, err = await svc._validate_files(
            raw_files=[{"file_id": "f_abc"}],
            text="@extract analyse",
            user=_make_user(),
        )

    assert result == []
    assert err is not None
    assert err["code"] == "FILE_STORAGE_UNAVAILABLE"
