from pydantic import BaseModel


# 🧠 Emotional & Cognitive Traits
class CognitiveProfile(BaseModel):
    optimism_level: float | None = 0.5  # [0.0 - 1.0]
    skepticism_level: float | None = 0.5  # [0.0 - 1.0]
    risk_tolerance: str | None = "moderate"  # e.g., "low", "reckless"
    alignment: str | None = "neutral"  # e.g., "chaotic-good"
    intelligence: str | None = "average"  # e.g., "genius"
    emotional_depth: float | None = 0.5
    creativity: float | None = 0.5
    humor: float | None = 0.5
    introversion: float | None = 0.5
    empathy_level: float | None = 0.5
    conflict_response: str | None = "diplomatic"
    attention_span: str | None = "medium"
    curiosity_level: float | None = 0.5
    learning_style: str | None = "logical"
    decision_biases: str | None = "data-driven"
