from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class CognitiveEventConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="COGNITIVE_EVENT_",
        extra="ignore",
    )

    signing_key: SecretStr
    exchange_name: str = "caos.events.v2"