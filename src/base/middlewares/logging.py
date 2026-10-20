import logging
import re
import time
import traceback
import uuid
from typing import Any

from fastapi import status
from starlette.datastructures import MutableHeaders
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.base.middlewares.request_context import reset_request_context, set_request_context

logger = logging.getLogger("logging middleware")


class LoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware for comprehensive logging of requests and responses.

    Logs detailed information about:
    - Request headers, query params, path params, client info
    - Response status, time, headers
    - Errors that occur during request processing
    """

    # Paths that should be excluded from logging
    EXCLUDED_PATHS = [
        r"^/$",
        r"^/docs",
        r"^/redoc",
        r"^/openapi.json",
        r"^/internal/",
        r"^/metrics",
        r"^/health",
        r"^/robots.*\.txt$",
    ]

    def is_path_excluded(self, path: str) -> bool:
        """Check if a path should be excluded from JWT verification."""
        for pattern in self.EXCLUDED_PATHS:
            if re.match(pattern, path):
                return True
        return False

    async def dispatch(self, request: Request, call_next):
        # Skip logging for excluded paths
        if self.is_path_excluded(request.url.path):
            return await call_next(request)

        # Reset context at the outermost middleware layer so each request starts clean.
        reset_request_context()

        # Use the correlation ID from the header if present, otherwise generate one.
        request_id = request.headers.get("x-correlation-id", str(uuid.uuid4()))

        # Store on request.state so FastAPI dependencies (get_request_id) can access it.
        request.state.request_id = request_id

        # Make request_id available to all log records for this request via RequestContextFilter.
        set_request_context("request_id", request_id)

        # Get start time to calculate duration
        start_time = time.time()

        # Gather request information
        request_info = await self._get_request_info(request)

        # Log the request
        logger.info(
            f"Request [{request_id}]: {request.method} {request.url.path}",
            extra={"request": request_info},
        )

        # Process the request and catch any errors
        try:
            response = await call_next(request)

            # Calculate request duration
            duration_ms = (time.time() - start_time) * 1000

            # Get response information
            response_info = self._get_response_info(response, duration_ms)

            # Log successful response
            log_level = logging.WARNING if response.status_code >= 400 else logging.INFO
            logger.log(
                log_level,
                f"Response [{request_id}]: {response.status_code} completed in {duration_ms:.2f}ms",
                extra={
                    "response": response_info,
                    "request_path": request.url.path,
                    "request_method": request.method,
                    "duration_ms": duration_ms,
                },
            )

            response.headers["x-correlation-id"] = request_id
            return response

        except Exception as exc:
            # Calculate duration for error case
            duration_ms = (time.time() - start_time) * 1000

            # Log the error with stack trace and request details
            logger.error(
                f"Error [{request_id}]: {str(exc)} during {request.method} {request.url.path}",
                extra={
                    "request": request_info,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                    "duration_ms": duration_ms,
                },
                exc_info=True,
            )

            # Re-raise the exception to let it be handled by exception handlers
            raise

    async def _get_request_info(self, request: Request) -> dict[str, Any]:
        """Extract comprehensive information from the request."""
        info = {
            "method": request.method,
            "url": str(request.url),
            "path": request.url.path,
            "path_params": dict(request.path_params),
            "query_params": dict(request.query_params),
            "client": {
                "host": request.client.host if request.client else "unknown",
                "port": request.client.port if request.client else "unknown",
            },
            "headers": self._get_sanitized_headers(request.headers),
        }

        # Get content type
        content_type = request.headers.get("content-type", "")

        # Try to extract request body for certain content types
        # Note: Body can only be read once, so we need to be careful
        if "application/json" in content_type:
            try:
                # Only attempt to read body for methods that might have one
                if request.method in ["POST", "PUT", "PATCH"]:
                    # Request body can only be read once, and this would interfere
                    # with route handlers, so we don't read it here in production code.
                    # If you really need body logging, you'd need a more complex approach
                    # that stores and resets request body content
                    pass
                    # DO NOT UNCOMMENT IN PRODUCTION:
                    # body = await request.json()
                    # info["body"] = body
            except Exception:
                info["body_parse_error"] = "Failed to parse JSON body"

        # Add auth information (if available) - be careful with sensitive data
        auth_header = request.headers.get("authorization")
        if auth_header:
            info["auth"] = {
                "type": auth_header.split()[0] if " " in auth_header else "unknown"
                # Don't log token values!
            }

        return info

    def _get_response_info(self, response: Response, duration_ms: float) -> dict[str, Any]:
        """Extract information from the response."""
        return {
            "status_code": response.status_code,
            "status_phrase": self._get_status_phrase(response.status_code),
            "headers": self._get_sanitized_headers(response.headers),
            "duration_ms": round(duration_ms, 2),
        }

    def _get_sanitized_headers(self, headers: MutableHeaders) -> dict[str, str]:
        """
        Extract headers while removing sensitive information.
        """
        sanitized = {}

        # Skip sensitive headers or truncate their values
        sensitive_headers = {
            "authorization",
            "cookie",
            "set-cookie",
            "x-api-key",
            "proxy-authorization",
            "x-forwarded-for",
        }

        for key, value in headers.items():
            key_lower = key.lower()
            if key_lower in sensitive_headers:
                # For auth headers, just keep the type
                if key_lower == "authorization" and " " in value:
                    sanitized[key] = f"{value.split(' ')[0]} [REDACTED]"
                else:
                    sanitized[key] = "[REDACTED]"
            else:
                sanitized[key] = value

        return sanitized

    def _get_status_phrase(self, status_code: int) -> str:
        """Get the standard phrase for an HTTP status code."""
        if status_code == status.HTTP_200_OK:
            return "OK"
        elif status_code == status.HTTP_201_CREATED:
            return "Created"
        elif status_code == status.HTTP_204_NO_CONTENT:
            return "No Content"
        elif status_code == status.HTTP_400_BAD_REQUEST:
            return "Bad Request"
        elif status_code == status.HTTP_401_UNAUTHORIZED:
            return "Unauthorized"
        elif status_code == status.HTTP_403_FORBIDDEN:
            return "Forbidden"
        elif status_code == status.HTTP_404_NOT_FOUND:
            return "Not Found"
        elif status_code == 422:
            return "Unprocessable Content"
        elif status_code == status.HTTP_500_INTERNAL_SERVER_ERROR:
            return "Internal Server Error"
        else:
            return "Unknown Status"
