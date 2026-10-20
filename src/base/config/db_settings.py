import logging
import os
from urllib.parse import parse_qs, quote_plus, urlencode, urlparse, urlunparse

from pydantic import ConfigDict
from pydantic_settings import BaseSettings


def _is_local_development() -> bool:
    return os.environ.get("ENVIRONMENT", "development").lower() == "development"


logger = logging.getLogger(__name__)


def _ensure_tls_true(uri: str) -> str:
    if not uri:
        return uri
    p = urlparse(uri)
    qs = parse_qs(p.query, keep_blank_values=True)
    if qs.get("tls", [""])[0].lower() != "true" and qs.get("ssl", [""])[0].lower() != "true":
        qs["tls"] = ["true"]
    return urlunparse(p._replace(query=urlencode(qs, doseq=True)))


def _escape_userinfo(uri: str) -> str:
    """Percent-encode user:password in the netloc if present."""
    if not uri or "mongodb://" not in uri:
        return uri
    p = urlparse(uri)
    if "@" not in p.netloc:
        return uri
    userinfo, hostpart = p.netloc.split("@", 1)
    if ":" in userinfo:
        user, pwd = userinfo.split(":", 1)
    else:
        user, pwd = userinfo, ""
    user_e = quote_plus(user) if user else ""
    pwd_e = quote_plus(pwd) if pwd else ""
    auth = f"{user_e}:{pwd_e}@" if (user_e or pwd_e) else ""
    return urlunparse(p._replace(netloc=auth + hostpart))


def _ensure_retry_writes_false(uri: str) -> str:
    if not uri:
        return uri
    parsed = urlparse(uri)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    # Cosmos usually requires retryWrites=false
    if qs.get("retryWrites", [""])[0].lower() != "false":
        qs["retryWrites"] = ["false"]
    new_query = urlencode(qs, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


class DatabaseSettings(BaseSettings):
    model_config = ConfigDict(
        frozen=True,
        env_file=".env",
        case_sensitive=False,
        extra="allow",
    )

    db_backend: str = "auto"
    db_name: str = "agentverse_local"
    db_uri: str = "mongodb://localhost:27017"

    @property
    def use_azure_cosmos(self) -> bool:
        backend = self.db_backend.lower()
        if backend == "cosmos":
            return True
        if backend == "mongo":
            return False
        # auto
        return not _is_local_development()

    @property
    def resolved_uri(self) -> str:
        if self.use_azure_cosmos:
            if not self.db_uri:
                raise ValueError("Azure Cosmos selected but db_uri is not set.")
            uri = _ensure_tls_true(_ensure_retry_writes_false(_escape_userinfo(self.db_uri)))
            logger.info("🔐 Using Azure Cosmos DB (Mongo API)")
            return uri
        logger.info("🧪 Using MongoDB")
        return self.db_uri

    @property
    def resolved_dbname(self) -> str:
        if self.use_azure_cosmos and self.db_name == "agentverse_local":
            logger.warning("DB_NAME not set; using value agentverse_local")
        return self.db_name
