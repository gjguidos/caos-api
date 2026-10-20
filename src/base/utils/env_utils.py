"""
Environment utilities for AgentVerse API.
"""

from src.base.config.config import settings


def is_local_development() -> bool:
    """
    Check if the application is running in local development environment.

    Returns:
        bool: True if running in local development, False otherwise.
    """
    environment = settings.application.environment.lower()
    return environment == "development"
