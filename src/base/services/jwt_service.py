
import logging
import os
from typing import Any

from src.base.authentication.auth_core import validate_jwt_token
from src.base.authentication.local_auth import (
    validate_local_jwt_token,
)

logger = logging.getLogger(__name__)


class JWTService:
    """Service for verifying JWT tokens."""

    async def verify_token(
        self,
        token: str,
    ) -> dict[str, Any] | None:
        try:
            auth_mode = os.environ.get(
                "AUTH_MODE", "entra"
            ).lower()

            if auth_mode == "local":
                return validate_local_jwt_token(token)

            if auth_mode == "entra":
                return await validate_jwt_token(token)

            raise ValueError(
                f"Unsupported AUTH_MODE: {auth_mode}"
            )

        except Exception as exc:
            logger.warning(
                "Token verification failed: %s",
                exc,
            )
            return None
