from regent.config import Settings
from regent.model import ModelConfigurationError, OpenAICompatibleProvider


def build_model_provider(settings: Settings) -> OpenAICompatibleProvider:
    if (
        settings.model_base_url is None
        or settings.model_name is None
        or settings.model_api_key is None
    ):
        raise ModelConfigurationError("model provider is not configured")
    if settings.model_provider != "openai-compatible":
        raise ModelConfigurationError(f"unsupported model provider: {settings.model_provider}")
    return OpenAICompatibleProvider(
        base_url=settings.model_base_url,
        api_key=settings.model_api_key.get_secret_value(),
        model=settings.model_name,
    )
