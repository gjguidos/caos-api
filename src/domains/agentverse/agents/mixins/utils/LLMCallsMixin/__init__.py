from src.domains.agentverse.agents.mixins.utils.LLMCallsMixin.chat_mixin import ChatLLMMixin
from src.domains.agentverse.agents.mixins.utils.LLMCallsMixin.image_mixin import ImageGenMixin
from src.domains.agentverse.agents.mixins.utils.LLMCallsMixin.transcribe_mixin import (
    TranscriptionLLMMixin,
)
from src.domains.agentverse.agents.mixins.utils.LLMCallsMixin.vision_mixin import VisionLLMMixin


class LLMCallsMixin(ChatLLMMixin, VisionLLMMixin, TranscriptionLLMMixin, ImageGenMixin):
    """
    Single public mixin that aggregates all LLM capabilities.
    Import this everywhere to avoid breaking existing imports.
    """

    pass


__all__ = [
    LLMCallsMixin,
]
