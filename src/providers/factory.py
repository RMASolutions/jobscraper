from .base import LLMProvider
from .gemini import GeminiProvider
from .openai import OpenAIProvider
from .anthropic import AnthropicProvider
from ..core.config import settings


def get_llm_provider(
    provider: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> LLMProvider:
    """
    Factory function to get the appropriate LLM provider.

    Args:
        provider: The provider name (gemini, openai, anthropic). Defaults to settings.
        api_key: Optional API key override.
        model: Optional model name override.

    Returns:
        An instance of the appropriate LLMProvider.
    """
    provider_name = provider or settings.llm_provider

    providers = {
        "gemini": GeminiProvider,
        "openai": OpenAIProvider,
        "anthropic": AnthropicProvider,
    }

    if provider_name not in providers:
        raise ValueError(
            f"Unknown provider: {provider_name}. "
            f"Available providers: {list(providers.keys())}"
        )

    return providers[provider_name](api_key=api_key, model=model)
