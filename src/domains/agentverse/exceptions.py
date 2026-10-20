from fastapi import status

from src.base.handlers.exception import DomainException


class AgentverseDomainException(DomainException):
    """
    Base exception for all errors occurring within the AgentVerse domain.

    Inherits from DomainException to ensure consistent FastAPI error response formatting.
    """

    domain = "agentverse"


class AgentVerseError(Exception):
    """
    Base Python-side exception (non-HTTP) for AgentVerse internal logic errors.
    This is intended for use in situations not tied directly to HTTP responses.
    """

    def __init__(self, message: str, details: dict = None):
        self.message = message
        self.details = details or {}
        super().__init__(message)


# ─────────────────────────────────────────────────────────────
# REGISTRY EXCEPTIONS
# ─────────────────────────────────────────────────────────────
class RegistryError(AgentVerseError):
    """General error raised during registry operations."""

    pass


class RegistrationError(RegistryError):
    """Raised when registration of a component fails."""

    pass


# ─────────────────────────────────────────────────────────────
# DOMAIN-SPECIFIC EXCEPTIONS
# ─────────────────────────────────────────────────────────────
class DuplicateAgentError(AgentverseDomainException):
    """
    Raised when attempting to create an EVA (Agent) that already exists.

    Example:
        - Trying to register an agent with a name that's already taken.
    """

    def __init__(self, field: str, value: str, message: str = None):
        custom_message = message or f"EVA DNA String with {field} '{value}' already presents in AEI"
        super().__init__(
            message=custom_message,
            status_code=status.HTTP_409_CONFLICT,
            error_code="AGENT_DUPLICATE",
            data={"field": field, "value": value},
        )


class BlueprintConflictError(DuplicateAgentError):
    """Alias for duplicate agent detection in blueprinting phase."""

    pass


class AgentAccessDeniedError(AgentverseDomainException):
    """
    Raised when a user attempts to access, update, or delete an agent
    they do not have permission to operate on.
    """

    def __init__(self, user_id: str, agent_id: str, action: str, reason: str = None):
        """
        Args:
            user_id: The ID of the user who was denied access
            agent_id: The ID of the agent they tried to access
            action: The action attempted (read, update, delete)
            reason: Optional reason for denial
        """
        message = (
            f"Access denied: user '{user_id}' is not authorized to {action} agent '{agent_id}'"
        )
        if reason:
            message += f" ({reason})"

        super().__init__(
            message=message,
            status_code=status.HTTP_403_FORBIDDEN,
            error_code="AGENT_ACCESS_DENIED",
            data={
                "user_id": user_id,
                "agent_id": agent_id,
                "action": action,
                "reason": reason,
            },
        )


class AgentNotFoundError(AgentverseDomainException):
    """
    Raised when an agent is not found in the database.
    """

    def __init__(self, agent_id: str):
        """
        Args:
            agent_id: The ID of the agent that was not found
        """
        super().__init__(
            message=f"Agent '{agent_id}' not found",
            status_code=status.HTTP_404_NOT_FOUND,
            error_code="AGENT_NOT_FOUND",
            data={"agent_id": agent_id},
        )


class InvalidComponentError(AgentverseDomainException):
    """
    Raised when one or more configuration types (e.g., LLM, DB, cache)
    provided for an EVA agent are not valid or not available in the system.
    """

    def __init__(self, invalid_fields: list[tuple[str, str]]):
        """
        Args:
            invalid_fields (list of tuples): Each tuple is (field, value) of the invalid entry.
        """
        formatted = ", ".join([f"{field}='{value}'" for field, value in invalid_fields])
        message = f"Invalid configuration for EVA: {formatted}"

        super().__init__(
            message=message,
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="INVALID_EVA_COMPONENT",
            data={
                "invalid_components": [
                    {"field": field, "value": value} for field, value in invalid_fields
                ]
            },
        )


class AgentValidationError(AgentverseDomainException):
    """
    Raised when an agent update or creation request fails validation
    (e.g. invalid tools, unknown role, missing rule packs, invalid groups).
    """

    def __init__(self, message: str, data: dict = None):
        super().__init__(
            message=message,
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="AGENT_VALIDATION_ERROR",
            data=data or {},
        )


class PDFIngestionError(Exception):
    """Raised when a tool or orchestration pipeline fails during execution."""

    pass


# ─────────────────────────────────────────────────────────────
# WORKFLOW EXCEPTIONS
# ─────────────────────────────────────────────────────────────
class WorkflowNotFoundError(AgentverseDomainException):
    """Raised when a workflow row is not found in the catalog."""

    def __init__(self, key: str):
        super().__init__(
            message=f"Workflow '{key}' not found",
            status_code=status.HTTP_404_NOT_FOUND,
            error_code="WORKFLOW_NOT_FOUND",
            data={"key": key},
        )


class WorkflowValidationError(AgentverseDomainException):
    """
    Raised when a workflow definition fails save-time validation
    (Pydantic shape, DAG structure, or unknown tool references).

    ``errors`` is a structured list so the canvas can highlight the
    offending step / tool ids.
    """

    def __init__(self, message: str, errors: list[dict] | None = None):
        super().__init__(
            message=message,
            status_code=422,
            error_code="WORKFLOW_VALIDATION_ERROR",
            data={"errors": errors or []},
        )


class WorkflowInUseError(AgentverseDomainException):
    """Raised when deleting a workflow that is still pinned by one or more agents."""

    def __init__(self, key: str, agent_ids: list[str]):
        super().__init__(
            message=(
                f"Workflow '{key}' is pinned by {len(agent_ids)} agent(s) and cannot be deleted"
            ),
            status_code=status.HTTP_409_CONFLICT,
            error_code="WORKFLOW_IN_USE",
            data={"key": key, "agent_ids": agent_ids},
        )
