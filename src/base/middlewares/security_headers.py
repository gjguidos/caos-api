"""
Security Headers Middleware.

This middleware adds essential security headers to all HTTP responses to protect against
various web security vulnerabilities and attacks. Each header serves a specific security purpose:

1. Strict-Transport-Security (HSTS): Forces HTTPS connections
2. Content-Security-Policy (CSP): Controls which resources can be loaded
3. X-Content-Type-Options: Prevents MIME type sniffing
4. X-Frame-Options: Protects against clickjacking
5. X-XSS-Protection: Enables browser's XSS filtering
6. Referrer-Policy: Controls how much referrer information should be sent
7. Permissions-Policy: Controls browser features and APIs

For more information about security headers, visit:
https://owasp.org/www-project-secure-headers/
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Middleware that adds security headers to all HTTP responses.

    This middleware implements defense in depth by adding various security headers
    that protect against common web vulnerabilities and attacks.

    Security Headers Added:
    - Strict-Transport-Security: Forces HTTPS usage
    - Content-Security-Policy: Controls resource loading
    - X-Content-Type-Options: Prevents MIME sniffing
    - X-Frame-Options: Prevents clickjacking
    - X-XSS-Protection: Enables XSS filtering
    - Referrer-Policy: Controls referrer information
    - Permissions-Policy: Restricts browser features
    """

    async def dispatch(self, request: Request, call_next):
        """
        Process the request and add security headers to the response.

        Args:
            request: The incoming HTTP request
            call_next: The next middleware or route handler

        Returns:
            Response with added security headers
        """
        response: Response = await call_next(request)

        # HSTS: Force HTTPS connections
        # max-age: How long the browser should remember to use HTTPS (2 years)
        # includeSubDomains: Apply to all subdomains
        # preload: Allow preloading in browser HSTS lists
        response.headers["Strict-Transport-Security"] = (
            "max-age=63072000; includeSubDomains; preload"
        )

        # Content-Security-Policy: Control resource loading
        # This policy defines which resources can be loaded and from where
        response.headers["Content-Security-Policy"] = (
            # Only allow resources from same origin by default
            "default-src 'self'; "
            # Allow scripts from same origin, inline scripts, and CDN
            "script-src 'self' 'unsafe-inline' cdn.jsdelivr.net blob:; "
            # Allow styles from same origin, inline styles, and CDN
            "style-src 'self' 'unsafe-inline' cdn.jsdelivr.net; "
            # Allow images from same origin, data URIs, and FastAPI's domain
            "img-src 'self' data: fastapi.tiangolo.com; "
            # Allow fonts from same origin, data URIs, and CDN
            "font-src 'self' data: cdn.jsdelivr.net; "
            # Allow API requests to same origin
            "connect-src 'self' https://login.microsoftonline.com; "
            # Allow Web Workers from blob URLs
            "worker-src 'self' blob:; "
            # Prevent site from being embedded in frames
            "frame-ancestors 'none'; "
            # Restrict form submissions to same origin
            "form-action 'self';"
        )

        # Prevent browsers from MIME-sniffing
        # This stops browsers from trying to guess the content type
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Prevent site from being embedded in frames (legacy header)
        # This provides protection against clickjacking attacks
        response.headers["X-Frame-Options"] = "DENY"

        # Enable browser's XSS filtering
        # mode=block: Block rendering rather than sanitize when XSS is detected
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Control how much referrer information should be included
        # no-referrer: Never send referrer information
        response.headers["Referrer-Policy"] = "no-referrer"

        # Control browser features and APIs
        # This header restricts access to sensitive browser features
        response.headers["Permissions-Policy"] = (
            "accelerometer=(), "  # Disable access to accelerometer
            "autoplay=(), "  # Disable automatic video playback
            "camera=(), "  # Disable access to camera
            "geolocation=(), "  # Disable access to geolocation
            "gyroscope=(), "  # Disable access to gyroscope
            "magnetometer=(), "  # Disable access to magnetometer
            "microphone=(), "  # Disable access to microphone
            "payment=(), "  # Disable access to payment APIs
            "usb=()"  # Disable access to USB devices
        )

        return response
