from __future__ import annotations

import logging

from dependency_injector import containers, providers

from src.base.config.config import settings
from src.base.services.file_service import FileService

logger = logging.getLogger(__name__)


def _create_file_service() -> FileService | None:
    cfg = settings.blob_storage

    if cfg.blob_storage_local_path:
        from src.base.services.local_file_service import LocalFileService

        logger.info(
            "Using LocalFileService at path=%s base_url=%s (dev mode)",
            cfg.blob_storage_local_path,
            cfg.blob_storage_local_base_url,
        )
        return LocalFileService(config=cfg)

    has_conn = bool(cfg.blob_storage_connection_string)
    has_url = bool(cfg.blob_storage_account_url)

    if not has_conn and not has_url:
        logger.warning(
            "Blob storage not configured (no connection string or account URL). "
            "File upload endpoints will be unavailable."
        )
        return None

    return FileService(config=cfg)


class BlobStorageContainer(containers.DeclarativeContainer):
    """Dependency injection container for Azure Blob Storage / FileService."""

    file_service = providers.Singleton(_create_file_service)
