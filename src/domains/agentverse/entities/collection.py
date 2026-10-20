from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# Placeholder written by earlier versions of this model. It never named a real
# embedding backend, so documents carrying it were embedded with the global
# default and are normalised back to None.
_UNSTAMPED_EMBEDDING_MODEL = "openai-embedding-v2"


class CollectionDocument(BaseModel):
    """
    MongoDB-backed model for vector-store collection metadata.
    """

    name: str
    agent_id: str
    owner_id: str
    visibility: Literal["private", "shared", "public"] = "private"
    allowed: list[str] = Field(default_factory=list)
    share_token: str | None = None

    # Per-collection tuning
    chunk_size: int = 1000
    chunk_overlap: int = 200
    # The embedding backend this collection's vectors were written with. None means
    # "the global default", which is what every collection created before this field
    # was stamped actually used.
    embedding_model: str | None = None
    vector_metric: str = "cosine"
    default_top_k: int = 5
    prompt_template: str = Field(
        default=(
            "You are an expert assistant. Use the following context to answer the question.\n\n"
            "Context:\n{context}\n\nQuestion: {query}\nAnswer:"
        )
    )

    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    @field_validator("embedding_model")
    @classmethod
    def _drop_unstamped_placeholder(cls, value: str | None) -> str | None:
        return None if value == _UNSTAMPED_EMBEDDING_MODEL else value


class CollectionCreateMetadata(BaseModel):
    visibility: Literal["private", "shared", "public"] = "private"
    chunk_size: int = 1000
    chunk_overlap: int = 200
    vector_metric: str = "cosine"
    prompt_template: str | None = None
    original_filename: str | None = None
