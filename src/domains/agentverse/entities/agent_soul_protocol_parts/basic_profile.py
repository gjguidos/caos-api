from pydantic import BaseModel


class EmotionalSignature(BaseModel):
    optimism: float | None = None
    empathy: float | None = None
    trust_tone: str | None = None


class BasicProfile(BaseModel):
    # Identity
    name: str | None = "Customized EVA"
    description: str | None = None
    archetype: list[str] | None = None
    primary_function: str | None = None

    # Communication / style
    tone: str | None = None
    voice_tone: str | None = None
    communication_style: str | None = None
    reasoning_style: str | None = None
    key_principles: list[str] | None = None
    humor: str | None = None
    language_register: str | None = None
    bias: str | None = None
    core_strengths: list[str] | None = None
    aesthetic_feel: str | None = None

    # Emotional signature
    emotional_signature: EmotionalSignature | None = None
