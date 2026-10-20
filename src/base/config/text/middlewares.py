class MiddlewareTexts:
    register = {
        "logger_name": "register middleware",
        "initialize_jwt_verification": "JWT verification middleware",
        "initialize_ip_filtering": "IP filtering middleware",
        "initialize_logging": "Logging middleware",
        "initialize_request": "Request ID middleware",
        "initialize_response_formatter": "Format response middleware",
        "initialize_security_headers": "Manage security headers middleware",
        "initialize_session": "Session middleware",
        "initialize_cors": "CORS middleware",
    }

    jwt_verification_middleware = {
        "logger_name": "JWT Middleware",
        "middleware_initialized": (
            "JWT Verification Middleware initialized with {excluded_paths} excluded paths"
        ),
        "verifying_request": "Verifying request",
        "excluding_paths": "Excluding paths",
        "excluded_path": "Excluded path {excluded_path}",
        "no_header": "No {auth_header} header in request to {url_path}",
        "missing_header_msg": "Authorization header missing",
        "invalid_format": ("Invalid {auth_header}header format in request to {url_path}"),
        "invalid_format_msg": "Invalid authorization header format",
        "invalid_token": "Invalid JWT token in request to {url_path}",
        "invalid_token_msg": "Invalid or expired token",
        "missing_sub": "JWT token missing 'sub' claim in request to {url_path}",
        "error_processing_token": "Error processing JWT token: {error}",
        "error_processing_token_msg": "Error processing authentication token",
    }

    ip_filter = {}

    logging = {}

    request_id = {}

    response = {}
