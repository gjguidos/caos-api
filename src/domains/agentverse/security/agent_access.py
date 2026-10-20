"""
Agent access control.

All read/write authorization decisions for agents live here.

Read rules:
    1. Public agents are visible to everyone.
    2. Private agents are always visible to their creator.
    3. Private agents with whitelisted groups are visible to members of those groups.
    4. Superadmins (agentverse-admin role) can see all agents.

Write rules (create, update, delete):
    1. The creator of the agent.
    2. Admins of any whitelisted group the agent belongs to.
    3. Superadmins.

Group membership is derived from user roles which follow the format
'<group>.<suffix>' (e.g. 'team-A.admin', 'team-A.user').
No external API calls are made.
"""

from src.base.entities.user import User
from src.domains.agentverse.entities.agent import Agent
from src.domains.agentverse.logging.logger_v1 import AGENTVERSE_LOGGER, LogDomain

SUPERADMIN_ROLE = "agentverse-admin"


# ---------------------------------------------------------------------------
# Role helpers
# ---------------------------------------------------------------------------


def is_superadmin(user: User) -> bool:
    """Return True if the user holds the superadmin role."""
    return SUPERADMIN_ROLE in user.roles


def get_user_groups(user: User) -> set[str]:
    """Return all group names the user belongs to, derived from their roles.

    Example: roles ['team-A.admin', 'team-B.user'] → {'team-A', 'team-B'}
    """
    return {role.split(".")[0] for role in user.roles if "." in role}


def get_user_admin_groups(user: User) -> set[str]:
    """Return group names where the user has admin access.

    Example: roles ['team-A.admin', 'team-B.user'] → {'team-A'}
    """
    return {role.rsplit(".", 1)[0] for role in user.roles if role.endswith(".admin")}


# ---------------------------------------------------------------------------
# Access checks
# ---------------------------------------------------------------------------


def can_read(user: User, agent: Agent) -> bool:
    """Return True if *user* may read *agent*.

    Evaluated in order - first match wins:
      1. Public agents are visible to all.
      2. Superadmins can see everything.
      3. Creators always see their own agents.
      4. Members of a whitelisted group can see the agent.
    """
    user_id = user.id
    agent_id = getattr(agent, "id", None)
    agent_name = getattr(agent, "name", None)

    if agent.visibility == "public":
        AGENTVERSE_LOGGER.info(
            LogDomain.SECURITY_BAY,
            "Read granted.",
            extra={
                "decision": "granted",
                "action": "read",
                "reason": "public_agent",
                "user_id": user_id,
                "target_agent_id": agent_id,
                "target_agent_name": agent_name,
                "visibility": agent.visibility,
            },
        )
        return True

    if is_superadmin(user):
        AGENTVERSE_LOGGER.info(
            LogDomain.SECURITY_BAY,
            "Read granted.",
            extra={
                "decision": "granted",
                "action": "read",
                "reason": "superadmin_role",
                "user_id": user_id,
                "target_agent_id": agent_id,
                "target_agent_name": agent_name,
                "visibility": agent.visibility,
            },
        )
        return True

    if agent.creator == user.id:
        AGENTVERSE_LOGGER.info(
            LogDomain.SECURITY_BAY,
            "Read granted.",
            extra={
                "decision": "granted",
                "action": "read",
                "reason": "creator_owns_agent",
                "user_id": user_id,
                "target_agent_id": agent_id,
                "target_agent_name": agent_name,
                "creator": agent.creator,
                "visibility": agent.visibility,
            },
        )
        return True

    if agent.whitelisted_groups:
        user_groups = get_user_groups(user)
        matching_groups = user_groups & set(agent.whitelisted_groups)
        if matching_groups:
            AGENTVERSE_LOGGER.info(
                LogDomain.SECURITY_BAY,
                "Read granted.",
                extra={
                    "decision": "granted",
                    "action": "read",
                    "reason": "whitelisted_group_member",
                    "user_id": user_id,
                    "target_agent_id": agent_id,
                    "target_agent_name": agent_name,
                    "visibility": agent.visibility,
                    "matching_groups": sorted(matching_groups),
                },
            )
            return True

    AGENTVERSE_LOGGER.warning(
        LogDomain.SECURITY_BAY,
        "Read denied.",
        extra={
            "decision": "denied",
            "action": "read",
            "user_id": user_id,
            "target_agent_id": agent_id,
            "target_agent_name": agent_name,
            "visibility": agent.visibility,
            "creator": agent.creator,
            "whitelisted_groups": agent.whitelisted_groups,
            "user_groups": sorted(get_user_groups(user)),
            "user_roles": user.roles,
        },
    )
    return False


def can_write(user: User, agent: Agent) -> bool:
    """Return True if *user* may create, update, or delete *agent*.

    Evaluated in order - first match wins:
      1. Superadmins can write everything.
      2. Creators own their agents.
      3. Group admins can write agents whitelisted to their group.
    """
    user_id = user.id
    agent_id = getattr(agent, "id", None)
    agent_name = getattr(agent, "name", None)

    if is_superadmin(user):
        AGENTVERSE_LOGGER.info(
            LogDomain.SECURITY_BAY,
            "Write granted.",
            extra={
                "decision": "granted",
                "action": "write",
                "reason": "superadmin_role",
                "user_id": user_id,
                "target_agent_id": agent_id,
                "target_agent_name": agent_name,
            },
        )
        return True

    if agent.creator == user.id:
        AGENTVERSE_LOGGER.info(
            LogDomain.SECURITY_BAY,
            "Write granted.",
            extra={
                "decision": "granted",
                "action": "write",
                "reason": "creator_owns_agent",
                "user_id": user_id,
                "target_agent_id": agent_id,
                "target_agent_name": agent_name,
                "creator": agent.creator,
            },
        )
        return True

    if agent.whitelisted_groups:
        user_admin_groups = get_user_admin_groups(user)
        matching_admin_groups = user_admin_groups & set(agent.whitelisted_groups)
        if matching_admin_groups:
            AGENTVERSE_LOGGER.info(
                LogDomain.SECURITY_BAY,
                "Write granted.",
                extra={
                    "decision": "granted",
                    "action": "write",
                    "reason": "group_admin",
                    "user_id": user_id,
                    "target_agent_id": agent_id,
                    "target_agent_name": agent_name,
                    "matching_admin_groups": sorted(matching_admin_groups),
                },
            )
            return True

    AGENTVERSE_LOGGER.warning(
        LogDomain.SECURITY_BAY,
        "Write denied.",
        extra={
            "decision": "denied",
            "action": "write",
            "user_id": user_id,
            "target_agent_id": agent_id,
            "target_agent_name": agent_name,
            "creator": agent.creator,
            "whitelisted_groups": agent.whitelisted_groups,
            "user_admin_groups": sorted(get_user_admin_groups(user)),
            "user_roles": user.roles,
        },
    )
    return False


# ---------------------------------------------------------------------------
# MongoDB filter builder
# ---------------------------------------------------------------------------


def build_read_filter(user: User) -> dict:
    """Return a MongoDB query that matches only agents *user* may read.

    Superadmins receive an empty filter (match all documents).
    Everyone else gets an $or covering:
      - public agents
      - agents they created
      - private agents whitelisted to one of their groups
    """
    user_id = user.id

    if is_superadmin(user):
        AGENTVERSE_LOGGER.info(
            LogDomain.SECURITY_BAY,
            "Read filter built.",
            extra={
                "action": "build_read_filter",
                "user_id": user_id,
                "reason": "superadmin_role",
                "filter_mode": "match_all",
            },
        )
        return {}

    conditions: list[dict] = [
        {"visibility": "public"},
        {"creator": user.id},
    ]

    user_group_ids = list(get_user_groups(user))
    if user_group_ids:
        conditions.append({"whitelisted_groups": {"$in": user_group_ids}})

    result_filter = {"$or": conditions}

    AGENTVERSE_LOGGER.info(
        LogDomain.SECURITY_BAY,
        "Read filter built.",
        extra={
            "action": "build_read_filter",
            "user_id": user_id,
            "reason": "standard_user_filter",
            "groups": user_group_ids,
            "filter_conditions": len(conditions),
        },
    )
    return result_filter
