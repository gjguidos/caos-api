from pydantic import BaseModel


# 🛠️ Simulation Metadata & Symbolism
class PersonalProfile(BaseModel):
    attachment_style: str | None = None
    hobbies: list[str] | None = None
    pregnancy_status: bool | None = False
    marital_status: str | None = "single"
    gender_identity: str | None = "neutral"
    artistic_preferences: list[str] | None = None
    favorite_colors: list[str] | None = None
    traumas: list[str] | None = None
    biases: list[str] | None = None
    country_of_origin: str | None = None
    country_of_residence: str | None = None
    place_of_recidence: str | None = None
    place_of_birth: str | None = None
    time_of_birth: str | None = None
    myer_briggs_type: str | None = None
    birth_year: int | None = None
    birth_month: int | None = None
    birth_day: int | None = None
    origin: str | None = None
