
"""
Local JWT authentication.

For local development only.
"""

import os
from typing import Any

from jose import JWTError, jwt


def validate_local_jwt_token(token: str) -> dict[str, Any]:
    secret = os.environ.get("LOCAL_JWT_SECRET")

    if not secret or len(secret) < 32:
        raise RuntimeError(
            "LOCAL_JWT_SECRET must contain at least 32 characters"
        )

    if os.environ.get("ENVIRONMENT", "").lower() not in {
        "development",
        "local",
    }:
        raise RuntimeError(
            "Local authentication is restricted to development"
        )

    try:
        return jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            issuer="CAOS-local",
            audience="CAOS-api",
            options={
                "require_exp": True,
                "require_iss": True,
                "require_aud": True,
                "require_sub": True,
            },
        )
    except JWTError:
        raise
