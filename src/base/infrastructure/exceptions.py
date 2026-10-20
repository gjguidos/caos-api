"""
Infrastructure-specific exceptions.
"""

from fastapi import status

from src.base.handlers.exception import DomainException


class InfrastructureException(DomainException):
    """Base exception for infrastructure-related errors."""

    domain = "infrastructure"


class DatabaseConnectionError(InfrastructureException):
    """Exception raised when a database connection fails."""

    def __init__(self, db_type: str, host: str, error_details: str = None, message: str = None):
        """
        Initialize the exception.

        Args:
            db_type: The type of database (e.g., 'mongodb', 'redis')
            host: The hostname or connection string
            error_details: Technical details about the error
            message: Optional custom message
        """
        if message is None:
            message = f"Failed to connect to {db_type} at {host}"

        super().__init__(
            message=message,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            error_code="DATABASE_CONNECTION_ERROR",
            data={"db_type": db_type, "host": host, "error_details": error_details},
        )


class MongoDBConnectionError(DatabaseConnectionError):
    """Exception specific to MongoDB connection failures."""

    def __init__(self, host: str, error_details: str = None, message: str = None):
        """
        Initialize the exception.

        Args:
            host: The MongoDB hostname or connection string
            error_details: Technical details about the error
            message: Optional custom message
        """
        super().__init__(db_type="mongodb", host=host, error_details=error_details, message=message)
        self.error_code = "MONGODB_CONNECTION_ERROR"


class RedisConnectionError(DatabaseConnectionError):
    """Exception specific to Redis connection failures."""

    def __init__(self, host: str, error_details: str = None, message: str = None):
        """
        Initialize the exception.

        Args:
            host: The Redis hostname or connection string
            error_details: Technical details about the error
            message: Optional custom message
        """
        super().__init__(db_type="redis", host=host, error_details=error_details, message=message)
        self.error_code = "REDIS_CONNECTION_ERROR"
