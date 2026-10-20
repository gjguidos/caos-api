import logging
from functools import wraps

from fastapi import HTTPException, Request

from src.base.middlewares.utils.session_utils import get_session_key

logger = logging.getLogger(__name__)


def extract_domain_from_path(path: str) -> str:
    """
    Extracts the domain from the URL path.

    This assumes your URL pattern is like:
      /api/v1/{domain}/endpoint
    and will return '{domain}'.
    """
    parts = path.strip("/").split("/")
    if len(parts) >= 3:
        return parts[2]
    # Fallback: if the pattern is different, return the last segment
    return parts[-1] if parts else ""


def session_based(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        # Find the request object
        request: Request = kwargs.get("request")
        if not request:
            for arg in args:
                if isinstance(arg, Request):
                    request = arg
                    break
        if not request:
            raise HTTPException(status_code=500, detail="Request object not found.")

        # Extract JWT token (already validated by middleware)
        auth_header = request.headers.get("Authorization", "")
        jwt_token = auth_header.replace("Bearer ", "")

        # Generate and store session key
        domain = extract_domain_from_path(request.url.path)
        session_key = get_session_key(jwt_token=jwt_token, domain=domain)

        # Store in request.state (not app.state to avoid collisions)
        request.state.session_key = session_key
        logger.info(f"[🔐 HTTP SESSION] Session key resolved: {session_key}")

        return await func(*args, **kwargs)

    return wrapper
