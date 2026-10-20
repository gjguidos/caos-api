import logging

from src.base.middlewares.utils.session_utils import get_session_key

logger = logging.getLogger("session handler middleware")


class SessionMiddleware:
    def __init__(self, app):
        self.app = app
        self.whitelisted_endpoints = []

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            # Define a send wrapper
            async def send_wrapper(message):
                if message["type"] == "http.response.start":
                    # Now the endpoint might be available in scope
                    endpoint = scope.get("endpoint")
                    logger.info(f"Endpoint in send_wrapper: {endpoint}")
                await send(message)

            # You can still try to check early (it may be None)
            endpoint = scope.get("endpoint")
            logger.info(f"Endpoint at __call__: {endpoint}")

            # You might perform early session logic here if you can,
            # or postpone it until later via the wrapped send.
            if endpoint and getattr(endpoint, "__session_based__", False):
                headers = dict(scope.get("headers", []))
                auth_header = headers.get(b"authorization", b"").decode("utf-8")
                if auth_header.startswith("Bearer "):
                    jwt_token = auth_header.replace("Bearer ", "")
                    session_key = get_session_key(jwt_token)
                    scope["session_key"] = session_key
                    self.whitelisted_endpoints.append(scope.get("path", ""))

            await self.app(scope, receive, send_wrapper)
        else:
            await self.app(scope, receive, send)
