"""Explicit resource ownership, independent of exposure and visibility.

Missing/invalid ownership is unclassified. Never infer it from names, tags,
creator IDs, tenant IDs or bootstrap approvals. Only trusted provisioning or a
reviewed migration may author the declaration.
"""

from collections.abc import Mapping
from typing import Any, Literal

from src.base.entities.user import User

ResourceOwnership = Literal["system", "tenant", "global"]


def valid_tenant(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def verified_tenant(user: User | None) -> str | None:
    if (
        isinstance(user, User)
        and isinstance(user.id, str)
        and user.id.strip()
        and valid_tenant(user.tenant_id)
    ):
        return user.tenant_id
    return None


def ownership_status(metadata: Mapping[str, Any]) -> str:
    """Stable inventory reason; contains no resource content or inferred owner."""
    ownership = metadata.get("ownership")
    tenant = metadata.get("tenant_id")
    if ownership == "tenant":
        return "tenant" if valid_tenant(tenant) else "invalid_tenant"
    if ownership in ("global", "system"):
        if tenant is not None or metadata.get("tenant_scoped"):
            return "conflicting_tenant"
        return ownership
    return "unclassified" if ownership is None else "invalid_ownership"


def client_owns_scope(user: User | None, metadata: Mapping[str, Any]) -> bool:
    status = ownership_status(metadata)
    return status == "global" or (
        status == "tenant" and verified_tenant(user) == metadata["tenant_id"]
    )


def inventory_ownership(records: list[dict[str, Any]], *, source: str) -> list[dict[str, Any]]:
    """Read-only, deterministic projection for trusted JSON/DB/registry exports.

    No writes, inferred assignments, prompts, credentials or resource bodies.
    Keep the input snapshot/hash alongside this projection for audit/CAS review.
    """
    return [
        {
            "source": source,
            "key": row.get("key") or row.get("id"),
            "ownership": row.get("ownership"),
            "tenant_id": row.get("tenant_id"),
            "status": ownership_status(row),
        }
        for row in records
    ]
