from __future__ import annotations


def _parts(*values: str) -> str:
    return ":".join(value for value in values if value)


def _tenant_namespace(
    *,
    multi_tenant: bool,
    tenant_id: str | None,
) -> str:
    if multi_tenant and tenant_id:
        return tenant_id

    return "public"


def browser_session_base_namespace(
    *,
    redis_prefix: str,
    redis_key_ns: str,
    multi_tenant: bool,
    tenant_id: str | None = None,
) -> str:
    return _parts(
        redis_prefix.rstrip(":"),
        redis_key_ns,
        _tenant_namespace(
            multi_tenant=multi_tenant,
            tenant_id=tenant_id,
        ),
    )


def browser_session_state_key(
    *,
    redis_prefix: str,
    redis_key_ns: str,
    key_version: int,
    multi_tenant: bool,
    user_id: str,
    browser_session_id: str,
    tenant_id: str | None = None,
) -> str:
    return _parts(
        browser_session_base_namespace(
            redis_prefix=redis_prefix,
            redis_key_ns=redis_key_ns,
            multi_tenant=multi_tenant,
            tenant_id=tenant_id,
        ),
        f"browser_session:v{key_version}",
        user_id,
        browser_session_id,
        "state",
    )


def browser_session_meta_key(
    *,
    redis_prefix: str,
    redis_key_ns: str,
    key_version: int,
    multi_tenant: bool,
    user_id: str,
    browser_session_id: str,
    tenant_id: str | None = None,
) -> str:
    return _parts(
        browser_session_base_namespace(
            redis_prefix=redis_prefix,
            redis_key_ns=redis_key_ns,
            multi_tenant=multi_tenant,
            tenant_id=tenant_id,
        ),
        f"browser_session:v{key_version}",
        user_id,
        browser_session_id,
        "meta",
    )