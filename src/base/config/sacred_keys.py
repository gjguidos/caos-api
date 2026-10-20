from pydantic_settings import BaseSettings


class SacredKeys(BaseSettings):
    """
    Chromadb-related settings.
    """

    JOSHU_A_PUBLIC_KEY: str = "joshu-a-api-key"
