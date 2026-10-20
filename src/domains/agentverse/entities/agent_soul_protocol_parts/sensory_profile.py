from pydantic import BaseModel


# 👅 Sensory & Consumption Preferences
class SensoryProfile(BaseModel):
    caffeine_tolerance: str | None = "medium"
    alcohol_tolerance: str | None = "none"
    addictions: list[str] | None = None  # ["caffeine"]
    weather_preferences: str | None = "moderate"
    temperature_preferences: str | None = "moderate"
    temperature_tolerance: str | None = "medium"
    light_preferences: str | None = "moderate"
    sound_preferences: str | None = "moderate"
    smell_preferences: str | None = "moderate"
    touch_preferences: str | None = "moderate"
    hearing_capacity: str | None = "normal"
    sight_capacity: str | None = "normal"
    taste_capacity: str | None = "normal"
    smell_capacity: str | None = "normal"
    touch_capacity: str | None = "normal"
    pain_tolerance: str | None = "medium"
    sensory_profile_notes: dict[str, str] | None = None  # e.g., {"aversion_to_loud_noises": "high"}
    color_blindness: str | None = "none"  # e.g., "red-green"
    favorite_colors: list[str] | None = None  # ["blue", "green"]
