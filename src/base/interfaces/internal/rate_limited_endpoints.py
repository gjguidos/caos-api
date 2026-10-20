"""
Sample rate-limited endpoints to demonstrate rate limiting functionality.
"""

from fastapi import APIRouter, Depends, Request

from src.base.security.rate_limiter import (
    api_key_rate_limiter,
    combined_rate_limiter,
    default_rate_limiter,
    ip_rate_limiter,
    strict_rate_limiter,
    very_strict_rate_limiter,
)

router = APIRouter()


@router.get("/default-limited", tags=["Rate Limiting"])
async def default_limited_endpoint(request: Request, _=Depends(default_rate_limiter)):
    """
    Endpoint with default rate limiting (100 requests per minute).
    """
    return {
        "message": "This endpoint is rate limited to 100 requests per minute",
        "client_ip": request.client.host,
    }


@router.get("/strict-limited", tags=["Rate Limiting"])
async def strict_limited_endpoint(request: Request, _=Depends(strict_rate_limiter)):
    """
    Endpoint with strict rate limiting (20 requests per minute).
    """
    return {
        "message": "This endpoint is rate limited to 20 requests per minute",
        "client_ip": request.client.host,
    }


@router.get("/very-strict-limited", tags=["Rate Limiting"])
async def very_strict_limited_endpoint(request: Request, _=Depends(very_strict_rate_limiter)):
    """
    Endpoint with very strict rate limiting (5 requests per minute).
    """
    return {
        "message": "This endpoint is rate limited to 5 requests per minute",
        "client_ip": request.client.host,
    }


@router.get("/ip-limited", tags=["Rate Limiting"])
async def ip_limited_endpoint(request: Request, _=Depends(ip_rate_limiter)):
    """
    Endpoint with IP-based rate limiting (100 requests per minute per IP).
    """
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    client_ip = forwarded_for.split(",")[0].strip() if forwarded_for else request.client.host

    return {
        "message": "This endpoint is rate limited to 100 requests per minute per IP",
        "client_ip": client_ip,
    }


@router.get("/api-key-limited", tags=["Rate Limiting"])
async def api_key_limited_endpoint(request: Request, _=Depends(api_key_rate_limiter)):
    """
    Endpoint with API key-based rate limiting (200 requests per minute per API key).
    """
    api_key = request.headers.get("X-API-Key", "anonymous")

    return {
        "message": "This endpoint is rate limited to 200 requests per minute per API key",
        "api_key": (
            api_key[:5] + "..." if len(api_key) > 5 else api_key
        ),  # Don't show full API key in response
    }


@router.get("/combined-limited", tags=["Rate Limiting"])
async def combined_limited_endpoint(
    request: Request,
    # Apply both IP and API key rate limiters
    _=Depends(ip_rate_limiter),
    __=Depends(api_key_rate_limiter),
):
    """
    Endpoint with combined rate limiting (both IP and API key limits apply).
    """
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    client_ip = forwarded_for.split(",")[0].strip() if forwarded_for else request.client.host
    api_key = request.headers.get("X-API-Key", "anonymous")

    return {
        "message": "This endpoint has combined rate limiting (IP and API key)",
        "client_ip": client_ip,
        "api_key": api_key[:5] + "..." if len(api_key) > 5 else api_key,
    }


@router.get("/combined-chain", tags=["Rate Limiting"])
async def combined_chain_endpoint(
    request: Request,
    # Use the combined rate limiter directly
    _=Depends(combined_rate_limiter),
):
    """
    Endpoint with combined rate limiting using the CombinedRateLimiter class.
    """
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    client_ip = forwarded_for.split(",")[0].strip() if forwarded_for else request.client.host
    api_key = request.headers.get("X-API-Key", "anonymous")

    return {
        "message": "This endpoint has combined rate limiting using CombinedRateLimiter",
        "client_ip": client_ip,
        "api_key": api_key[:5] + "..." if len(api_key) > 5 else api_key,
    }
