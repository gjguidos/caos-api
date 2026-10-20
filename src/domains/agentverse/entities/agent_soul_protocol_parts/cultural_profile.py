from pydantic import BaseModel


# 🌍 Cultural & Social Orientation
class CulturalProfile(BaseModel):
    cultural_background: str | None = None  # e.g., "Cyberpunk"
    language_proficiency: dict[str, str] | None = None  # {"en": "native"}
    family_values: str | None = "independent"
    media_consumption: list[str] | None = None  # ["anime", "mythology"]
    political_alignment: str | None = "apolitical"
    social_attitude: str | None = "egalitarian"
    group_affiliation: str | None = None
    social_media_usage: str | None = "moderate"
    social_media_platforms: list[str] | None = None  # ["twitter", "discord"]
    musical_preferences: list[str] | None = None
    artistic_preferences: list[str] | None = None
    favorite_movies: list[str] | None = None
    favorite_books: list[str] | None = None
    favorite_artists: list[str] | None = None
    favorite_authors: list[str] | None = None
    favorite_quotes: list[str] | None = None
