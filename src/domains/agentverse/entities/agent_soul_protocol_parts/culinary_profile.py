from pydantic import BaseModel


# 🧬 Culinary Details
class CulinaryProfile(BaseModel):
    food_preferences: list[str] | None = None  # e.g., ["vegan", "gluten-free"]
    cuisine_preferences: list[str] | None = None  # e.g., ["Italian", "Mexican"]
    dietary_restrictions: list[str] | None = None  # e.g., ["nut allergy", "lactose intolerant"]
    cooking_skills: str | None = "beginner"  # e.g., "beginner", "intermediate", "expert"
    protein_source_preferences: list[str] | None = None  # e.g., ["chicken", "tofu"]
    cooking_style: str | None = "home_cooked"  # e.g., "home_cooked", "takeout"
    meal_preferences: list[str] | None = None  # e.g., ["breakfast", "snack"]
    meal_frequency: str | None = "3 meals a day"  # e.g., "2 meals a day", "5 meals a day"
    meal_time_preferences: list[str] | None = None  # e.g., ["morning", "evening"]
    meal_preparation_time: str | None = "30 minutes"  # e.g., "15 minutes", "1 hour"
    meal_portion_size: str | None = "medium"  # e.g., "small", "large"
    salt_level_preference: str | None = "medium"  # e.g., "low", "high"
    spice_level_preference: str | None = "medium"  # e.g., "low", "high"
    sugar_level_preference: str | None = "medium"  # e.g., "low", "high"
    sour_level_preference: str | None = "medium"  # e.g., "low", "high"
    texture_preferences: list[str] | None = None  # e.g., ["crispy", "smooth"]
    salt_tolerance: str | None = "medium"  # e.g., "low", "high"
    spice_tolerance: str | None = "medium"  # e.g., "low", "high"
    sugar_tolerance: str | None = "medium"  # e.g., "low", "high"
    sour_tolerance: str | None = "medium"  # e.g., "low", "high"
    texture_tolerance: str | None = "medium"  # e.g., "low", "high"
