from pydantic_settings import BaseSettings, SettingsConfigDict


class LoggerConfig(BaseSettings):
    """Logging and Splunk HEC settings."""

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    splunk_hec_token: str | None = None
    splunk_host: str | None = None
    log_level: str = "INFO"
    log_show_extras: bool = False
