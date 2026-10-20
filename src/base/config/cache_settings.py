from __future__ import annotations

from urllib.parse import quote, urlparse, urlunparse

from pydantic import ConfigDict, field_validator, model_validator
from pydantic_settings import BaseSettings


class CacheSettings(BaseSettings):
    """
    Cache-related settings (e.g., Redis).
    """

    model_config = ConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )

    # Connection
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_username: str | None = None
    redis_password: str | None = None
    redis_tls: bool = True  # if True, prefer rediss://

    # Leave room for concurrent crawler fetches, stream workers, and the
    # other Redis-backed services sharing this client.
    redis_pool_max: int = 64
    redis_connect_timeout_ms: int = 2000
    redis_read_timeout_ms: int = 3000
    redis_db: int = 0
    redis_url: str | None = None

    # Namespacing
    redis_prefix: str = "myapp"  # no trailing colon
    redis_key_ns: str = "av"  # logical namespace (product/domain)

    # Agent keys
    redis_agent_key_version: int = 1
    redis_agent_spawn_ttl: int = 3600
    redis_agent_key_multi_tenant: bool = False

    # History keys
    redis_history_key_version: int = 1
    redis_history_ttl: int = 604800
    redis_history_key_multi_tenant: bool = False
    redis_history_max_items: int = 500

    # Session keys
    redis_session_key_version: int = 1
    redis_session_ttl: int = 604800
    redis_session_key_multi_tenant: bool = False

    # Memory keys
    redis_memory_key_version: int = 1
    redis_memory_ttl: int | None = None

    # ---------- validators ----------
    @field_validator("redis_prefix")
    @classmethod
    def _no_trailing_colon(cls, v: str) -> str:
        return v.rstrip(":").strip()

    @field_validator(
        "redis_port",
        "redis_pool_max",
        "redis_connect_timeout_ms",
        "redis_read_timeout_ms",
        "redis_db",
        "redis_agent_spawn_ttl",
        "redis_history_ttl",
        "redis_session_ttl",
        "redis_history_max_items",
    )
    @classmethod
    def _positive_int(cls, v: int) -> int:
        if v < 0:
            raise ValueError("must be non-negative")
        return v

    def _build_auth_string(self) -> str:
        """Build authentication string for Redis URL."""
        if not (self.redis_username or self.redis_password):
            return ""

        usr = quote(self.redis_username or "", safe="")
        pwd = f":{quote(self.redis_password or '', safe='')}" if self.redis_password else ""
        return f"{usr}{pwd}@"

    def _build_url_from_parts(self) -> str:
        """Build Redis URL from individual connection parameters."""
        scheme = "rediss" if self.redis_tls else "redis"
        port = self.redis_port or (6380 if self.redis_tls else 6379)
        auth = self._build_auth_string()
        return f"{scheme}://{auth}{self.redis_host}:{port}/{self.redis_db}"

    def _align_url_scheme(self) -> None:
        """Align existing URL scheme with redis_tls setting."""
        p = urlparse(self.redis_url)
        desired = "rediss" if self.redis_tls else "redis"
        if self.redis_tls is not None and p.scheme != desired:
            p = p._replace(scheme=desired)
            self.redis_url = urlunparse(p)

    @model_validator(mode="after")
    def _normalize(self):
        """
        Normalize/derive redis_url. If redis_url provided, we align the scheme
        with redis_tls when explicitly set.
        """
        if not self.redis_url:
            self.redis_url = self._build_url_from_parts()
        else:
            self._align_url_scheme()

        return self
