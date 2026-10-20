
from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class AiModelsConfig(BaseSettings):
    model_config = ConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="allow",
        frozen=False,
    )

    llm_provider: str = "auto"

    # --- Azure OpenAI ---
    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""

    azure_chat_deployments: str = ""
    azure_embedding_deployments: str = ""
    azure_whispering_deployments: str = ""
    azure_image_deployments: str = ""

    # --- OpenAI API ---
    openai_api_key: str = ""
    openai_url: str = "https://api.openai.com/v1"

    # --- OpenAI Models ---
    openai_chat_models: str = (
        "gpt-6-astra,"
        "gpt-6.1-sol,"
        "gpt-6.1-sol-ultrafast,"
        "gpt-6-luna"
    )

    openai_image_models: str = (
        "gpt-image-2.5-sunburst,"
        "gpt-image-2.5-flare,"
        "gpt-image-2"
    )

    openai_realtime_models: str = (
        "gpt-realtime-2.1,"
        "gpt-realtime-2.1-mini"
    )

    openai_audio_models: str = "gpt-audio-1.5"

    openai_transcription_models: str = (
        "gpt-transcribe,"
        "gpt-live-transcribe"
    )

    openai_embedding_models: str = (
        "text-embedding-3-large,"
        "text-embedding-3-small"
    )

    # --- OpenAI Defaults ---
    default_chat_model: str = "gpt-6-astra"
    default_chat_model_mini: str = "gpt-6.1-sol"
    default_fast_model: str = "gpt-6.1-sol-ultrafast"
    default_lightweight_model: str = "gpt-6-luna"

    default_image_model: str = "gpt-image-2.5-sunburst"
    default_realtime_model: str = "gpt-realtime-2.1"
    default_audio_model: str = "gpt-audio-1.5"
    default_whisper_model: str = "gpt-transcribe"
    default_embedding_model: str = "text-embedding-3-large"

    # --- Provider Detection ---

    @property
    def is_azure_available(self) -> bool:
        return bool(
            self.azure_openai_api_key.strip()
            and self.azure_openai_endpoint.strip()
        )

    @property
    def is_openai_available(self) -> bool:
        return bool(
            self.openai_api_key.strip()
            and self.openai_url.strip()
        )

    @property
    def is_azure_configured(self) -> bool:
        return self.is_azure_available

    # --- Automatic Provider Resolution ---

    @property
    def resolved_default_llm(self) -> str:
        provider = self.llm_provider.strip().lower()

        if provider == "openai":
            if not self.is_openai_available:
                raise RuntimeError("OpenAI is not configured")
            return "openai"

        if provider == "azure":
            if not self.is_azure_available:
                raise RuntimeError("Azure OpenAI is not configured")
            return "azure"

        if provider != "auto":
            raise ValueError(f"Invalid LLM provider: {provider}")

        if self.is_openai_available:
            return "openai"

        if self.is_azure_available:
            return "azure"

        raise RuntimeError("No valid LLM backend configured")

    @property
    def resolved_default_audio(self) -> str:
        return self.resolved_default_llm

    # --- Parsing Helpers ---

    @staticmethod
    def _parse_models(value: str) -> list[str]:
        return [
            item.strip()
            for item in value.split(",")
            if item.strip()
        ]

    # --- Azure Deployments ---

    @property
    def parsed_azure_chat_deployments(self) -> list[str]:
        return self._parse_models(self.azure_chat_deployments)

    @property
    def parsed_azure_embedding_deployments(self) -> list[str]:
        return self._parse_models(self.azure_embedding_deployments)

    @property
    def parsed_azure_whispering_deployments(self) -> list[str]:
        return self._parse_models(self.azure_whispering_deployments)

    @property
    def parsed_azure_image_deployments(self) -> list[str]:
        return self._parse_models(self.azure_image_deployments)

    # --- OpenAI Models ---

    @property
    def parsed_openai_chat_models(self) -> list[str]:
        return self._parse_models(self.openai_chat_models)

    @property
    def parsed_openai_image_models(self) -> list[str]:
        return self._parse_models(self.openai_image_models)

    @property
    def parsed_openai_realtime_models(self) -> list[str]:
        return self._parse_models(self.openai_realtime_models)

    @property
    def parsed_openai_audio_models(self) -> list[str]:
        return self._parse_models(self.openai_audio_models)

    @property
    def parsed_openai_transcription_models(self) -> list[str]:
        return self._parse_models(self.openai_transcription_models)

    @property
    def parsed_openai_embedding_models(self) -> list[str]:
        return self._parse_models(self.openai_embedding_models)
