from pydantic import BaseModel


# ☯️ Spiritual & Belief System
class SpiritualProfile(BaseModel):
    religiosity: str | None = None  # e.g., "hermetic"
    philosophical_inclination: str | None = None  # e.g., "stoic"
    conspiracy_theories: list[str] | None = None  # ["moon landing"]
    chinese_zodiac_sign: str | None = None
    chinese_element: str | None = None
    zodiac_polarity: str | None = None
    zodiac_fixed_trait: str | None = None
    western_zodiac_sign: str | None = None
    element: str | None = None
    spiritual_experiences: list[str] | None = None
    past_lives: list[str] | None = None
    resurrection_level: float | None = 0.0
    is_mortal: bool | None = True
