"""
JWT service dependency module.

This module provides dependencies for JWT authentication to be used in
FastAPI routes.
"""

import logging

from dependency_injector.wiring import Provide, inject
from fastapi import Depends, HTTPException, Request, status

from src.base.dependencies.di_container import Container
from src.base.entities.user import User
from src.base.services.jwt_service import JWTService

logger = logging.getLogger(__name__)


@inject
def get_jwt_service(
    jwt_service: JWTService = Depends(Provide[Container.services.jwt_service]),
) -> JWTService:
    """
    Dependency that provides a JWTService instance.

    Args:
        jwt_service: The JWT service from the dependency injection container

    Returns:
        JWTService: A configured JWT service instance
    """
    return jwt_service


def get_current_user_from_state(request: Request) -> User:
    """
    Get the authenticated user information from the request state.
    This is to be used with the JWTVerificationMiddleware which sets
    the user information in the request state.

    Args:
        request: The FastAPI request object

    Returns:
        User: The authenticated user information

    Raises:
        HTTPException: If the user is not authenticated
    """
    if not hasattr(request.state, "user") or request.state.user is None:
        logger.warning(
            "Attempted to access protected route without\
                       authentication"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return request.state.user


async def get_user_id_from_state(request: Request) -> str:
    """
    Get the authenticated user ID from the request state.
    Convenience dependency for when you only need the user ID.

    Args:
        request: The FastAPI request object

    Returns:
        str: The authenticated user ID

    Raises:
        HTTPException: If the user is not authenticated
    """
    user = await get_current_user_from_state(request)
    return user.id


async def get_user_roles_from_state(request: Request) -> list[str]:
    """
    Get the authenticated user roles from the request state.
    Convenience dependency for when you only need the user roles.

    Args:
        request: The FastAPI request object

    Returns:
        list[str]: The authenticated user roles

    Raises:
        HTTPException: If the user is not authenticated
    """
    user = await get_current_user_from_state(request)
    return user.roles
