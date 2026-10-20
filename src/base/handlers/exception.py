import logging
import platform
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.base.config.config import settings

# Create a logger instance
logger = logging.getLogger(__name__)


class DomainException(Exception):
    """
    Base exception for domain-specific errors.
    Use this for business logic exceptions with custom status codes.

    Example:
        raise DomainException(
            message="User not authorized to perform this action",
            status_code=403,
            error_code="UNAUTHORIZED_ACTION"
        )
    """

    domain = "base"  # Override this in domain-specific subclasses

    def __init__(
        self,
        message: str,
        status_code: int = 400,
        error_code: str = "DOMAIN_ERROR",
        data: dict[str, Any] | None = None,
    ):
        """
        Initialize a domain exception.

        Args:
            message: The error message for the user
            status_code: HTTP status code to return
            error_code: A machine-readable error code
            data: Additional data to include in the error response
        """
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.data = data or {}
        super().__init__(self.message)


GENERIC_ERROR_MESSAGE = "Service temporarily unavailable, please try again later."
GENERIC_ERROR_CODE = "INTERNAL_ERROR"


def classify_error(exc: BaseException) -> tuple[str, str]:
    """Map an exception to a ``(user_safe_message, error_code)`` pair.

    ``DomainException`` is user-safe, so its own message and code are returned.
    Any other exception is collapsed to a generic message so internal detail
    never leaks to the client. Callers are responsible for logging the original.
    """
    if isinstance(exc, DomainException):
        return exc.message, exc.error_code
    return GENERIC_ERROR_MESSAGE, GENERIC_ERROR_CODE


def _is_crawler_probe(path: str) -> bool:
    return path.startswith("/robots") and path.endswith(".txt")


def _is_localhost(request: Request) -> bool:
    """
    Check if the request is from localhost.

    Args:
        request: The FastAPI request object

    Returns:
        bool: True if the request is from localhost
    """
    host = request.headers.get("host", "")
    return host.startswith("localhost:") or host.startswith("127.0.0.1:")


def register_exception_handlers(app: FastAPI) -> None:
    """
    Register global exception handlers for the FastAPI app.

    Args:
        app (FastAPI): The FastAPI application instance.
    """

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        """Handle FastAPI HTTPException with detailed logging."""
        context = get_request_context(request)
        logger.error(f"HTTPException: {exc.detail}", extra=context)

        response = {"success": False, "statusCode": exc.status_code, "message": exc.detail}

        # Add development information for localhost
        if _is_localhost(request):
            response["pythonVersion"] = platform.python_version()
            response["systemVersion"] = platform.platform()

        # Add documentation links for unauthenticated responses
        response["OpenApi-JSON-Documentation"] = f"{request.base_url}v3/\
            api-docs"
        response["OpenApi-Documentation"] = f"{request.base_url}swagger-ui/\
            index.html"

        return JSONResponse(
            status_code=exc.status_code,
            content=response,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        """Handle request validation errors with user-friendly formatting."""
        context = get_request_context(request)
        errors = format_validation_errors(exc.errors())

        logger.warning(f"Validation error: {errors}", extra=context)

        response = {
            "success": False,
            "statusCode": 422,
            "message": "Validation error",
            "errors": errors,
        }

        # Add development information for localhost
        if _is_localhost(request):
            response["pythonVersion"] = platform.python_version()
            response["systemVersion"] = platform.platform()

        # Add documentation links for unauthenticated responses
        response["OpenApi-JSON-Documentation"] = f"{request.base_url}v3/\
            api-docs"
        response["OpenApi-Documentation"] = f"{request.base_url}swagger-ui/\
            index.html"

        return JSONResponse(
            status_code=422,
            content=response,
        )

    @app.exception_handler(ValidationError)
    async def pydantic_validation_exception_handler(request: Request, exc: ValidationError):
        """Handle Pydantic validation errors with user-friendly formatting."""
        context = get_request_context(request)
        errors = format_validation_errors(exc.errors())

        logger.warning(f"Pydantic validation error: {errors}", extra=context)

        response = {
            "success": False,
            "statusCode": 422,
            "message": "Data validation error",
            "errors": errors,
        }

        # Add development information for localhost
        if _is_localhost(request):
            response["pythonVersion"] = platform.python_version()
            response["systemVersion"] = platform.platform()

        # Add documentation links for unauthenticated responses
        response["OpenApi-JSON-Documentation"] = f"{request.base_url}v3/\
            api-docs"
        response["OpenApi-Documentation"] = f"{request.base_url}swagger-ui/\
            index.html"

        return JSONResponse(
            status_code=422,
            content=response,
        )

    @app.exception_handler(StarletteHTTPException)
    async def starlette_http_exception_handler(request: Request, exc: StarletteHTTPException):
        context = get_request_context(request)
        path = request.url.path

        # --- 404 handling (NOT an error) ---
        if exc.status_code == 404:
            if _is_crawler_probe(path):
                logger.debug("Crawler probe 404: %s", path, extra=context)
            else:
                logger.info("404 Not Found: %s", path, extra=context)

            return JSONResponse(
                status_code=404,
                content={"success": False, "statusCode": 404, "message": "Not Found"},
            )

        # --- everything else ---
        logger.warning(
            f"HTTP {exc.status_code}: {exc.detail}",
            extra=context,
        )

        response = {
            "success": False,
            "statusCode": exc.status_code,
            "message": exc.detail,
        }

        if _is_localhost(request):
            response["pythonVersion"] = platform.python_version()
            response["systemVersion"] = platform.platform()

        return JSONResponse(
            status_code=exc.status_code,
            content=response,
        )

    @app.exception_handler(DomainException)
    async def domain_exception_handler(request: Request, exc: DomainException):
        """Handle custom domain-specific exceptions."""
        context = get_request_context(request)
        logger.warning(
            f"Domain exception ({exc.domain}.{exc.error_code}): {exc.message}"
            + (f" | details: {exc.data}" if exc.data else ""),
            extra={
                **context,
                "domain": exc.domain,
                "error_code": exc.error_code,
                "error_data": exc.data,
            },
        )

        response = {"success": False, "statusCode": exc.status_code, "message": exc.message}

        # Include domain error details
        if exc.data:
            response["data"] = exc.data

        # Add error code information
        response["error"] = {"code": f"{exc.domain}.{exc.error_code}", "domain": exc.domain}

        # Add development information for localhost
        if _is_localhost(request):
            response["pythonVersion"] = platform.python_version()
            response["systemVersion"] = platform.platform()

        # Add documentation links for unauthenticated responses
        response["OpenApi-JSON-Documentation"] = f"{request.base_url}v3/\
            api-docs"
        response["OpenApi-Documentation"] = f"{request.base_url}swagger-ui/\
            index.html"

        return JSONResponse(
            status_code=exc.status_code,
            content=response,
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        """Global exception handler for unhandled exceptions."""
        context = get_request_context(request)
        logger.error(
            f"Unhandled exception:\
                     {str(exc)}",
            extra=context,
            exc_info=True,
        )

        response = {"success": False, "statusCode": 500}

        # In development, you might want to see the actual error
        if settings.application.environment.lower() == "development" or _is_localhost(request):
            response["message"] = str(exc)
            response["type"] = exc.__class__.__name__
            response["pythonVersion"] = platform.python_version()
            response["systemVersion"] = platform.platform()
        else:
            # In production, use a generic message
            response["message"] = "An unexpected error occurred."

        # Add documentation links for unauthenticated responses
        response["OpenApi-JSON-Documentation"] = f"{request.base_url}v3/\
            api-docs"
        response["OpenApi-Documentation"] = f"{request.base_url}swagger-ui/\
            index.html"

        return JSONResponse(
            status_code=500,
            content=response,
        )


def get_request_context(request: Request | WebSocket) -> dict[str, str]:
    """
    Extract useful context information from the request or websocket.
    """
    base_context = {
        "request_id": getattr(request.state, "request_id", ""),
        "path": request.url.path,
        "client_ip": request.client.host if request.client else "unknown",
        "user_agent": request.headers.get("user-agent", "unknown"),
        "referer": request.headers.get("referer", "none"),
    }

    if isinstance(request, Request):
        base_context["method"] = request.method
    else:
        base_context["method"] = "WEBSOCKET"

    return base_context


def format_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, str]]:
    """
    Format validation errors to be more user-friendly.

    Args:
        errors: List of pydantic error dictionaries

    Returns:
        List of formatted error dictionaries
    """
    formatted_errors = []
    for error in errors:
        # Get the field name from the location
        field = None
        if "loc" in error:
            loc = error["loc"]
            # Skip the first element if it's 'body' or 'query'
            start_idx = 1 if len(loc) > 1 and loc[0] in ("body", "query", "path", "header") else 0
            field = (
                ".".join(str(x) for x in loc[start_idx:]) if len(loc) > start_idx else str(loc[0])
            )

        formatted_errors.append(
            {
                "field": field,
                "message": error.get("msg", "Unknown validation error"),
                "type": error.get("type", "unknown_error"),
            }
        )

    return formatted_errors
