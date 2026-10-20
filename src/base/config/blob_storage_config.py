from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class BlobStorageConfig(BaseSettings):
    """Configuration for Azure Blob Storage integration.

    Azure container lifecycle policy (set via Terraform / portal — not code):
    Delete blobs older than 1 hour to backstop missed per-turn cleanups.
    """

    blob_storage_account_url: str = Field(
        default="",
        description="Azure Storage account URL (e.g. https://<account>.blob.core.windows.net)",
    )
    blob_storage_account_name: str = Field(
        default="",
        description="Storage account name (derived from account_url if omitted)",
    )
    blob_storage_account_key: str = Field(
        default="",
        description="Storage account key (for SAS generation and auth)",
    )
    blob_storage_connection_string: str = Field(
        default="",
        description="Full connection string; overrides account_url + account_key when set",
    )
    blob_storage_container_name: str = Field(
        default="agentverse-files",
        description="Blob container name",
    )
    blob_storage_use_managed_identity: bool = Field(
        default=False,
        description="Use Azure managed identity instead of key-based auth",
    )
    blob_storage_local_path: str = Field(
        default="",
        description=(
            "Local filesystem path for file storage (dev only). "
            "When set, bypasses Azure Blob Storage entirely."
        ),
    )
    blob_storage_local_base_url: str = Field(
        default="http://agentverse:8000",
        description=(
            "Base URL used to construct local file serve URLs (dev only). "
            "Set to http://localhost:8000 when running outside Docker."
        ),
    )

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"
