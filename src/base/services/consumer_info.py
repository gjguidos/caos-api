"""
Consumer Information Service.

This module provides functionality for managing and retrieving consumer information
that needs to be communicated to API consumers, such as deprecation notices,
termination notices, and other important messages.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any


class ConsumerInfoType(Enum):
    """Types of consumer information messages."""

    DEPRECATION = "deprecation"
    TERMINATION = "termination"
    MODIFICATION = "modification"
    IMPORTANT = "important"


class ConsumerInfoService:
    """Service for managing consumer information."""

    def __init__(self):
        # In a real implementation, this would be stored in a database
        # and managed through an admin interface
        self._consumer_info: list[dict[str, Any]] = []

    def add_deprecation_notice(
        self,
        deprecated_endpoint: dict[str, str],
        replacement_endpoint: dict[str, str],
        reason: str,
        end_of_life: str,
    ) -> None:
        """
        Add a deprecation notice for an endpoint.

        Args:
            deprecated_endpoint: Dict with 'path' and 'method' of the deprecated endpoint
            replacement_endpoint: Dict with 'path' and 'method' of the replacement endpoint
            reason: Explanation of why the endpoint is deprecated
            end_of_life: Date when the endpoint will be terminated (YYYY-MM-DD)
        """
        self._consumer_info.append(
            {
                "type": ConsumerInfoType.DEPRECATION.value,
                "deprecatedEndpoint": deprecated_endpoint,
                "replacementEndpoint": replacement_endpoint,
                "reason": reason,
                "endOfLife": end_of_life,
            }
        )

    def add_termination_notice(
        self, endpoint: dict[str, str], reason: str, end_of_life: str
    ) -> None:
        """
        Add a termination notice for an endpoint.

        Args:
            endpoint: Dict with 'path' and 'method' of the endpoint to be terminated
            reason: Explanation of why the endpoint is being terminated
            end_of_life: Date when the endpoint will be terminated (YYYY-MM-DD)
        """
        self._consumer_info.append(
            {
                "type": ConsumerInfoType.TERMINATION.value,
                "endpoint": endpoint,
                "reason": reason,
                "action": "This endpoint will not be replaced, only terminated",
                "endOfLife": end_of_life,
            }
        )

    def add_modification_notice(
        self, endpoint: dict[str, str], reason: str, end_of_life: str
    ) -> None:
        """
        Add a modification notice for an endpoint.

        Args:
            endpoint: Dict with 'path' and 'method' of the endpoint being modified
            reason: Explanation of the modification
            end_of_life: Date when the modification will take effect (YYYY-MM-DD)
        """
        self._consumer_info.append(
            {
                "type": ConsumerInfoType.MODIFICATION.value,
                "endpoint": endpoint,
                "reason": reason,
                "endOfLife": end_of_life,
            }
        )

    def add_important_message(self, message: str, end_of_life: str) -> None:
        """
        Add an important message for API consumers.

        Args:
            message: The important message to communicate
            end_of_life: Date when the message will expire (YYYY-MM-DD)
        """
        self._consumer_info.append(
            {"type": ConsumerInfoType.IMPORTANT.value, "message": message, "endOfLife": end_of_life}
        )

    def get_active_consumer_info(self) -> list[dict[str, Any]]:
        """
        Get all active consumer information messages.

        Returns:
            List of active consumer information messages
        """
        current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return [info for info in self._consumer_info if info["endOfLife"] >= current_date]

    def clear_expired_messages(self) -> None:
        """Remove all expired consumer information messages."""
        current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._consumer_info = [
            info for info in self._consumer_info if info["endOfLife"] >= current_date
        ]
