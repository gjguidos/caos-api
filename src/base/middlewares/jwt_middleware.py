"""
JWT Verification Middleware.

This middleware automatically verifies JWT tokens in requests and sets
authenticated user information in the request state for protected routes.
"""

import logging
import re
from typing import Any

from dependency_injector.wiring import inject
from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from src.base.config.config import settings
from src.base.entities.user import User
from src.base.logging.security_logger import log_unauthorized_access
from src.base.middlewares.request_context import set_request_context
from src.base.services.jwt_service import JWTService

logger = logging.getLogger(settings.texts_new.middleware.jwt_verification_middleware["logger_name"])


@inject
class JWTVerificationMiddleware(BaseHTTPMiddleware):
    """
    Middleware that automatically verifies JWT tokens in requests headers
    and makes the user information available in the request state.
    """

    def __init__(
        self,
        app: ASGIApp,
        jwt_service: JWTService,
        exclude_paths: list[str] = None,
        auth_header: str = "Authorization",
    ):
        """
        Initialize the JWT verification middleware.

        Args:
            app: The ASGI application
            jwt_service: The JWT service for token verification
            exclude_paths: List of path regex patterns to exclude from JWT verification
            auth_header: The header name to extract the JWT token from
        """
        super().__init__(app)
        self.jwt_service = jwt_service
        self.exclude_paths = exclude_paths or [
            r"^/$",
            r"^/docs",
            r"^/redoc",
            r"^/openapi.json",
            r"^/internal/",
            r"^/metrics",
            r"^/health",
            r"^/robots.*\.txt$",
            r"^/api/v1/dev/files/",
            r"^/api/v1/browser-sessions/callback",
            r"^/api/v1/browser-sessions/[^/]+/render/?$",
        ]
        self.auth_header = auth_header
        logger.info(
            settings.texts_new.middleware.jwt_verification_middleware[
                "middleware_initialized"
            ].format(excluded_paths=len(self.exclude_paths))
        )

    def is_path_excluded(self, path: str) -> bool:
        """Check if a path should be excluded from JWT verification."""
        for pattern in self.exclude_paths:
            if re.match(pattern, path):
                return True
        return False

    def _is_localhost(self, request: Request) -> bool:
        """
        Check if the request is coming from localhost.

        Args:
            request: The FastAPI request object

        Returns:
            bool: True if the request is from localhost
        """
        host = request.headers.get("host", "")
        return host.startswith("localhost:") or host.startswith("127.0.0.1:")

    async def _create_unauthorized_response(
        self,
        request: Request,
        reason: str,
        message_key: str,
        additional_info: dict[str, Any] | None = None,
    ) -> JSONResponse:
        """
        Create a standardized unauthorized response and log the attempt.

        Args:
            request: The FastAPI request object
            reason: The reason for unauthorized access
            message_key: The key for the error message in settings
            additional_info: Additional information to log

        Returns:
            JSONResponse with 401 status
        """
        # Log the unauthorized access attempt
        await log_unauthorized_access(
            request=request,
            reason=reason,
            additional_info=additional_info or {},
        )

        # Create the response
        response = {
            "success": False,
            "statusCode": status.HTTP_401_UNAUTHORIZED,
            "message": settings.texts_new.middleware.jwt_verification_middleware[message_key],
        }

        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=response,
            headers={"WWW-Authenticate": "Bearer"},
        )

    def _extract_token_from_header(self, auth_header: str) -> str | None:
        """
        Extract JWT token from Authorization header.

        Args:
            auth_header: The Authorization header value

        Returns:
            The JWT token if valid format, None otherwise
        """
        if not auth_header.startswith("Bearer "):
            return None
        return auth_header[len("Bearer ") :]

    async def dispatch(self, request: Request, call_next):
        """
        Process the request and verify JWT token if present.

        Args:
            request: The incoming request
            call_next: The next middleware or route handler in the chain

        Returns:
            The response from the next middleware or route handler
        """
        logger.debug(settings.texts_new.middleware.jwt_verification_middleware["verifying_request"])
        logger.debug(settings.texts_new.middleware.jwt_verification_middleware["excluding_paths"])

        # Don't verify JWT for excluded paths
        if self.is_path_excluded(request.url.path):
            logger.debug(
                settings.texts_new.middleware.jwt_verification_middleware["excluded_path"].format(
                    excluded_path=request.url.path
                )
            )
            return await call_next(request)

        # Extract the JWT token from the Authorization header
        auth_header = request.headers.get(self.auth_header)

        if not auth_header:
            logger.warning(
                settings.texts_new.middleware.jwt_verification_middleware["no_header"].format(
                    auth_header=self.auth_header, url_path=request.url.path
                )
            )

            return await self._create_unauthorized_response(
                request=request,
                reason="missing_auth_header",
                message_key="missing_header_msg",
                additional_info={"required_header": self.auth_header},
            )

        # Extract token from header
        token = self._extract_token_from_header(auth_header)
        if token is None:
            logger.warning(
                settings.texts_new.middleware.jwt_verification_middleware["invalid_format"].format(
                    auth_header=self.auth_header, url_path=request.url.path
                )
            )

            return await self._create_unauthorized_response(
                request=request,
                reason="invalid_auth_header_format",
                message_key="invalid_format_msg",
                additional_info={
                    "received_header": (
                        auth_header[:20] + "..." if len(auth_header) > 20 else auth_header
                    )
                },
            )

        # Verify the JWT token
        claims = await self.jwt_service.verify_token(token)
        if not claims:
            logger.warning(
                settings.texts_new.middleware.jwt_verification_middleware["invalid_token"].format(
                    auth_header=self.auth_header, url_path=request.url.path
                )
            )

            return await self._create_unauthorized_response(
                request=request,
                reason="invalid_token",
                message_key="invalid_token_msg",
                additional_info={"received_token": token[:20] + "..."},
            )

        # Store user information for dependencies (extract from claims)
        request.state.user = User(
            id=claims.get("oid") or claims.get("sub"),
            email=claims.get("email") or claims.get("preferred_username"),
            name=claims.get("name"),
            roles=claims.get("roles", []),
            scopes=claims.get("scp", "").split() if claims.get("scp") else [],
            access_token=token,
        )

        set_request_context("user_object_id", request.state.user.id or "")
        set_request_context("user_roles", ",".join(request.state.user.roles))

        # Continue processing the request
        return await call_next(request)
