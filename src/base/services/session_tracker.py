"""
Session Tracking Service.

This module provides a comprehensive session tracking system that leverages Redis
to record and analyze API usage patterns. It tracks requests and responses by client IP
and/or user ID, enabling analysis of user journeys, detection of suspicious activities,
and monitoring of API performance on a per-client basis.

Features:
- Client session identification by IP and/or user ID
- Request tracking with timestamps and paths
- Response tracking with status codes and performance metrics
- Historical data retention with configurable expiration
- User journey reconstruction capabilities
"""

import json
import time
import uuid
from typing import Any

from dependency_injector.wiring import Provide, inject

from src.base.config.config import settings
from src.base.dependencies.di_container import Container
from src.base.repositories.redis_repository import RedisRepository


class SessionTracker:
    """
    Service for tracking client sessions and requests in Redis.

    This service tracks client sessions across multiple requests by creating
    a unique session ID for each client (identified by IP and/or user ID). It then
    records details of each request and response associated with the session, enabling
    analysis of user journeys and API usage patterns.

    Session data is stored in Redis with automatic expiration to manage storage efficiently.
    """

    # Prefix for Redis keys to avoid collisions
    KEY_PREFIX = f"{settings.redis_prefix}:session:"
    # Default session expiration (2 hours)
    DEFAULT_EXPIRY = 7200

    @classmethod
    @inject
    async def record_request(
        cls,
        client_ip: str,
        user_id: str | None,
        request_path: str,
        request_method: str,
        request_id: str,
        redis_repository: RedisRepository = Provide[Container.redis_repository],
    ) -> str:
        """
        Record a new request in the client's session.

        This method creates or retrieves an existing session for the client and adds
        the current request to the session's history. It returns the session ID, which
        can be used to link the response to this request.

        Args:
            client_ip: The client's IP address
            user_id: Optional user ID if authenticated
            request_path: The endpoint path
            request_method: The HTTP method (GET, POST, etc.)
            request_id: Unique identifier for this request
            redis_repository: Redis repository (injected automatically)

        Returns:
            str: The session ID for the client
        """
        # Generate or retrieve session ID for this client
        session_id = await cls._get_or_create_session(client_ip, user_id, redis_repository)

        # Current timestamp
        timestamp = int(time.time())

        # Create request record
        request_data = {
            "timestamp": timestamp,
            "path": request_path,
            "method": request_method,
            "request_id": request_id,
        }

        # Add to the session's request list (limited to recent 100 requests)
        await redis_repository.lpush(
            f"{cls.KEY_PREFIX}{session_id}:requests", json.dumps(request_data)
        )
        await redis_repository.ltrim(f"{cls.KEY_PREFIX}{session_id}:requests", 0, 99)

        # Update session expiry
        await redis_repository.expire(f"{cls.KEY_PREFIX}{session_id}:requests", cls.DEFAULT_EXPIRY)
        await redis_repository.expire(f"{cls.KEY_PREFIX}{session_id}:info", cls.DEFAULT_EXPIRY)

        return session_id

    @classmethod
    @inject
    async def record_response(
        cls,
        session_id: str,
        request_id: str,
        status_code: int,
        duration_ms: float,
        redis_repository: RedisRepository = Provide[Container.redis_repository],
    ) -> None:
        """
        Record a response to a previous request.

        This method adds response information to the session history, allowing for complete
        request-response tracking with performance metrics.

        Args:
            session_id: The session ID returned from record_request
            request_id: The unique identifier for the original request
            status_code: The HTTP status code of the response
            duration_ms: The request processing time in milliseconds
            redis_repository: Redis repository (injected automatically)
        """
        # Current timestamp
        timestamp = int(time.time())

        # Create response record
        response_data = {
            "timestamp": timestamp,
            "request_id": request_id,
            "status_code": status_code,
            "duration_ms": duration_ms,
        }

        # Store in session
        await redis_repository.lpush(
            f"{cls.KEY_PREFIX}{session_id}:responses", json.dumps(response_data)
        )
        await redis_repository.ltrim(f"{cls.KEY_PREFIX}{session_id}:responses", 0, 99)
        await redis_repository.expire(f"{cls.KEY_PREFIX}{session_id}:responses", cls.DEFAULT_EXPIRY)

    @classmethod
    @inject
    async def get_session_history(
        cls,
        session_id: str,
        redis_repository: RedisRepository = Provide[Container.redis_repository],
    ) -> dict[str, Any]:
        """
        Get the full history for a session.

        This method retrieves all recorded information for a session, including
        session metadata, request history, and response history.

        Args:
            session_id: The session ID to retrieve
            redis_repository: Redis repository (injected automatically)

        Returns:
            Dict containing session data, or None if session not found
        """
        # Get session info
        session_info_json = await redis_repository.get(f"{cls.KEY_PREFIX}{session_id}:info")
        if not session_info_json:
            return None

        session_info = json.loads(session_info_json)

        # Get requests
        requests_json = await redis_repository.lrange(
            f"{cls.KEY_PREFIX}{session_id}:requests", 0, -1
        )
        requests = [json.loads(r) for r in requests_json]

        # Get responses
        responses_json = await redis_repository.lrange(
            f"{cls.KEY_PREFIX}{session_id}:responses", 0, -1
        )
        responses = [json.loads(r) for r in responses_json]

        return {
            "session_id": session_id,
            "info": session_info,
            "requests": requests,
            "responses": responses,
        }

    @classmethod
    @inject
    async def _get_or_create_session(
        cls,
        client_ip: str,
        user_id: str | None,
        redis_repository: RedisRepository = Provide[Container.redis_repository],
    ) -> str:
        """
        Get existing session for client or create a new one.

        This private method attempts to find an existing session for the client by user ID
        (if authenticated) or by IP address. If no session is found, it creates a new one.

        Args:
            client_ip: The client's IP address
            user_id: Optional user ID if authenticated
            redis_repository: Redis repository

        Returns:
            str: The session ID
        """
        # Try to find existing session by client IP and/or user ID
        session_key = None
        if user_id:
            # If user is authenticated, try to find by user ID
            session_key = await redis_repository.get(f"{cls.KEY_PREFIX}user:{user_id}")

        if not session_key and client_ip:
            # If no session found by user ID, try by IP
            session_key = await redis_repository.get(f"{cls.KEY_PREFIX}ip:{client_ip}")

        if session_key:
            return session_key

        # Create new session
        session_id = str(uuid.uuid4())

        # Store session info
        session_info = {"created_at": int(time.time()), "client_ip": client_ip, "user_id": user_id}

        # Save session info and mappings
        await redis_repository.set(
            f"{cls.KEY_PREFIX}{session_id}:info",
            json.dumps(session_info),
            expiration=cls.DEFAULT_EXPIRY,
        )

        # Map user ID to session if available
        if user_id:
            await redis_repository.set(
                f"{cls.KEY_PREFIX}user:{user_id}", session_id, expiration=cls.DEFAULT_EXPIRY
            )

        # Map IP to session
        if client_ip:
            await redis_repository.set(
                f"{cls.KEY_PREFIX}ip:{client_ip}", session_id, expiration=cls.DEFAULT_EXPIRY
            )

        return session_id
