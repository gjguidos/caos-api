from src.domains.agentverse.tools.base import ToolConfig


class IdeationToolConfig(ToolConfig):
    configurable: bool = True
    max_tokens: int = 700
    temperature: float = 0.6
    top_p: float = 1.0
    presence_penalty: float = 0.0
    frequency_penalty: float = 0.0
    history_window: int = 10
    budget_tokens: int = 4000
    list_style: str = "numbered"
    max_words_hint: int = 200
    default_mode: str = "cluster"
    default_num_ideas: int = 12
    allow_frameworks: list[str] = ["AIDA", "PAS", "4P"]
    platform_caps: dict = {
        "x": {"max_hashtags": 5, "max_char_length": 280},
        "instagram": {"max_hashtags": 30},
        "linkedin": {"max_hashtags": 5},
        "tiktok": {"max_hashtags": 10},
        "youtube": {"title_max": 100, "desc_max": 5000},
    }
    price_cents_per_call: int = 0
    deterministic_default_seed: int = 42
