from enum import Enum


class Role(Enum):
    """Available roles in the system"""

    USER = "agentverse-user"
    ADMIN = "agentverse-admin"
    GREEN_CLAIM_USER = "green-claim-user"

    @classmethod
    def get_all_roles(cls) -> list[str]:
        """Get all available role values"""
        return [role.value for role in cls]

    @classmethod
    def from_string(cls, role_str: str) -> "Role":
        """Convert string to Role enum, case insensitive"""
        try:
            return cls(role_str.lower())
        except ValueError:
            raise ValueError(f"Invalid role: {role_str}")
