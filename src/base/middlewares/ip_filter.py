"""
IP Filter Middleware.

This module provides IP address filtering for protected routes,
allowing access to be restricted to specific IP addresses for sensitive
endpoints such as admin panels and internal API endpoints.

The middleware can be configured with:
- A list of allowed IP addresses
- Protected paths that require IP validation
- Public paths that are exempt from IP restrictions
"""

import logging
import platform

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger("ip filter middleware")


class IPFilterMiddleware(BaseHTTPMiddleware):
    """
    Middleware to filter requests based on IP address.

    This middleware provides IP-based access control for protected routes,
    allowing you to restrict access to sensitive endpoints to specific IP addresses.
    It supports both protected paths (requiring IP validation) and public paths
    (exempt from IP restrictions).
    """

    def __init__(self, app, allowed_ips=None, protected_paths=None, public_paths=None):
        """
        Initialize the IP filter middleware.

        Args:
            app: The FastAPI application
            allowed_ips: List of allowed IP addresses for protected paths
            protected_paths: List of path prefixes that require IP validation
            public_paths: List of path prefixes that are exempt from IP validation
        """
        super().__init__(app)
        self.allowed_ips = allowed_ips or []
        self.protected_paths = protected_paths or ["/admin", "/internal"]
        self.public_paths = public_paths or ["/internal/metrics", "/metrics", "/internal/health"]

        # Log configuration
        logger.info(
            f"IP Filter Middleware configured with {len(self.allowed_ips)} allowed IPs, "
            f"{len(self.protected_paths)} protected paths, and {len(self.public_paths)} public paths"
        )

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

    async def dispatch(self, request: Request, call_next):
        """
        Process the request and apply IP filtering.

        This method:
        1. Checks if the path is in the public paths list (unrestricted access)
        2. If not public, checks if the path is in the protected paths list
        3. If protected, validates the client IP against the allowed IPs list
        4. If validation fails, returns a 403 Forbidden response
        5. Otherwise, continues processing the request

        Args:
            request: The incoming HTTP request
            call_next: The next middleware or route handler

        Returns:
            The HTTP response
        """
        client_ip = request.client.host if request.client else None

        # Check if the path is explicitly public (no IP restrictions)
        if any(request.url.path.startswith(path) for path in self.public_paths):
            return await call_next(request)

        # Check if the path is protected and IP is restricted
        if any(request.url.path.startswith(path) for path in self.protected_paths):
            if not self.allowed_ips or client_ip not in self.allowed_ips:
                logger.warning(
                    f"Access denied to {request.url.path} from IP {client_ip}. "
                    f"Not in allowed IPs list: {self.allowed_ips}"
                )

                response = {"success": False, "statusCode": 403, "message": "Access denied"}

                # Add development information for localhost
                if self._is_localhost(request):
                    response["pythonVersion"] = platform.python_version()
                    response["systemVersion"] = platform.platform()

                # Add documentation links
                response["OpenApi-JSON-Documentation"] = f"{request.base_url}v3/api-docs"
                response["OpenApi-Documentation"] = f"{request.base_url}swagger-ui/index.html"

                return JSONResponse(status_code=403, content=response)

        return await call_next(request)
