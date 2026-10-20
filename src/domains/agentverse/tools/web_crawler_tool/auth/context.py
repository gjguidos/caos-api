from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, SecretStr


class CrawlAuthContext(BaseModel):
    mode: str = "none"
    provider: str = "none"

    subject_user_id: str | None = None
    browser_session_id: str | None = None

    allowed_domains: list[str] = []
    storage_state_ref: SecretStr | None = None

    expires_at: datetime | None = None
    secrets_redacted: bool = True

    metadata: dict[str, Any] = {}

    def safe_metadata(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "provider": self.provider,
            "subject_user_id": self.subject_user_id,
            "browser_session_id": self.browser_session_id,
            "allowed_domains": self.allowed_domains,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "secrets_redacted": True,
            **(self.metadata or {}),
        }