from pydantic import BaseModel


# 🧬 Identity & Origin
class IdentityProfile(BaseModel):
    name: str | None = (
        "Customized EVA"  # Default identifier or archetype name, e.g., "Inanna", "Adam-01"
    )
    description: str | None = None  # Backstory or function summary
    origin: str | None = None  # e.g., "Mesopotamian Mythology"
