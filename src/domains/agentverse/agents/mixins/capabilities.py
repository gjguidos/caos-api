class CapabilitiesMixin:
    def _compute_capabilities(self) -> set[str]:
        caps = {"text_in", "text_out"}  # defaults

        def has(x):
            return bool(x) and not getattr(x, "is_noop", False)

        # Input
        if has(getattr(self, "image", None)) and getattr(self.llm, "supports_vision", True):
            caps.add("vision_in")
        if has(getattr(self, "audio", None)) and getattr(self.audio, "supports_asr", True):
            caps.add("audio_in")
        if has(getattr(self, "video", None)) and getattr(self.video, "supports_video_in", False):
            caps.add("video_in")

        # Output
        if has(getattr(self, "image", None)) and getattr(self.image, "supports_generation", True):
            caps.add("image_out")
        if has(getattr(self, "audio", None)) and getattr(self.audio, "supports_tts", True):
            caps.add("audio_out")
        if hasattr(self.llm, "supports_embeddings") and self.llm.supports_embeddings:
            caps.add("embeddings_out")

        # Infra/data
        if has(getattr(self, "cache", None)):
            caps.add("memory")
        if has(getattr(self, "vectordb", None)):
            caps.add("rag")
        if has(getattr(self, "messaging", None)):
            caps.add("messaging")

        return caps

    def supports(self, cap: str) -> bool:
        return cap in getattr(self, "capabilities", set())
