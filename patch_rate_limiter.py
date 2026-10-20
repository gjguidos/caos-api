#!/usr/bin/env python
"""
Patch to fix the rate limiter by directly modifying the rate limiter dependencies.py file.
"""

# Path to the file we're going to modify
DEPENDENCY_FILE = "/app/src/base/dependencies/rate_limiter_dependencies.py"


def patch_rate_limiter_dependencies():
    """Patch the rate limiter dependencies file to fix rate limiting"""
    print(f"Modifying {DEPENDENCY_FILE}...")

    with open(DEPENDENCY_FILE, "r") as f:
        content = f.read()

    # Add a direct call to FastAPILimiter.init in the dependency functions
    modified_content = content.replace(
        """@inject
async def get_standard_write_rate_limiter(
    limiter: Callable = Depends(Provide[Container.standard_write_rate_limiter])
) -> Callable:""",
        """@inject
async def get_standard_write_rate_limiter(
    limiter: Callable = Depends(Provide[Container.standard_write_rate_limiter])
) -> Callable:
    # Ensure FastAPILimiter is initialized on first use
    from fastapi_limiter import FastAPILimiter
    if not hasattr(FastAPILimiter, '_redis') or not FastAPILimiter._redis:
        print("Initializing FastAPILimiter directly in dependency...")
        from dependency_injector.wiring import Provide, inject
        from src.base.dependencies.di_container import Container
        redis_client = Container.redis_client()
        
        # Custom callback for rate limiting
        async def async_http_callback(request, response, pexpire):
            import platform
            from src.base.system.rate_limiter import ip_identifier
            endpoint = request.url.path
            identifier = ip_identifier(request)
            
            expire_seconds = round(pexpire / 1000.0, 1)
            print(f"Rate limit triggered on {endpoint} | Client: {identifier}")
            
            # Check if the request is from localhost
            host = request.headers.get("host", "")
            is_localhost = host.startswith("localhost:") or host.startswith("127.0.0.1:")
            
            # Standard rate limit response using the new format
            response.status_code = 429
            error_response = {
                "success": False,
                "statusCode": 429,
                "message": "Too Many Requests"
            }
            
            # Add development information for localhost
            if is_localhost:
                error_response["pythonVersion"] = platform.python_version()
                error_response["systemVersion"] = platform.platform()
            
            # Add documentation links
            error_response["OpenApi-JSON-Documentation"] = f"{request.base_url}v3/api-docs"
            error_response["OpenApi-Documentation"] = f"{request.base_url}swagger-ui/index.html"
            
            return error_response
        
        # Initialize the limiter
        await FastAPILimiter.init(
            redis_client,
            prefix="fastapi-limiter",
            http_callback=async_http_callback
        )
        print("FastAPILimiter initialized directly in dependency")""",
    )

    # Write the modified content back to the file
    with open(DEPENDENCY_FILE, "w") as f:
        f.write(modified_content)

    print("Modified the rate_limiter_dependencies.py file.")
    print("Now restart the API container to apply changes.")
    print("Run: docker compose restart api")


if __name__ == "__main__":
    patch_rate_limiter_dependencies()
