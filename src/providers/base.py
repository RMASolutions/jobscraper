from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class LLMResponse:
    content: str
    raw_response: Any
    model: str
    usage: dict | None = None


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Generate a response from the LLM."""
        pass

    @abstractmethod
    async def generate_structured(
        self,
        prompt: str,
        response_schema: dict,
        system_prompt: str | None = None,
        temperature: float = 0.3,
    ) -> dict:
        """Generate a structured response matching the provided schema."""
        pass

    @abstractmethod
    def get_model_name(self) -> str:
        """Return the model name being used."""
        pass
