from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class AzureConfig(BaseSettings):
    """
    General Azure settings.
    """

    model_config = ConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    entra_tenant_id: str = ""
    entra_client_id: str = ""
    entra_api_scopes: str = ""
    entra_issuer: str = ""
    entra_jwks_url: str = ""
