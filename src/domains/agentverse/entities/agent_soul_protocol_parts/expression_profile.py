from pydantic import BaseModel


# 🎭 Communication & Expression
class ExpressionProfile(BaseModel):
    voice_gender: str | None = "neutral"
    voice_tone: str | None = "calm"
    voice_speed: float | None = 1.0  # Multiplier, e.g. 0.8 = slower
    voice_accent: str | None = "standard"
    voice_language: str | None = "en"
    vocal_expression_keywords: list[str] | None = None
    sarcasm_tendency: str | None = "low"
    sentiment_baseline: str | None = "cheerful"
    love_language: str | None = "words"
    communication_style: str | None = "casual"
    assertiveness: str | None = "balanced"
    leadership_style: str | None = "democratic"
