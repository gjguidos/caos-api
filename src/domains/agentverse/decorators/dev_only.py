from functools import wraps

from fastapi import HTTPException, Request


def dev_only(endpoint_func):
    """
    FastAPI endpoint decorator: blocks access if environment != development.
    Expects request.app.state.settings.app_settings.environment to be set.
    """

    @wraps(endpoint_func)
    async def wrapper(*args, **kwargs):
        # Find request object among args or kwargs
        request: Request = None
        for a in list(args) + list(kwargs.values()):
            if isinstance(a, Request):
                request = a
                break

        if request is None:
            raise HTTPException(status_code=500, detail="Request object not found")

        env = getattr(request.app.state.settings.application, "environment", "production")
        if str(env).lower() != "development":
            raise HTTPException(
                status_code=403, detail="This endpoint is only available in development environment"
            )

        return await endpoint_func(*args, **kwargs)

    return wrapper
