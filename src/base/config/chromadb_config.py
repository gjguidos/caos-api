from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class ChromaDBSettings(BaseSettings):
    """
    Chromadb-related settings.
    """

    model_config = ConfigDict(frozen=True, env_file=".env", case_sensitive=False, extra="allow")
    chromadb_path: str = "/chroma"
