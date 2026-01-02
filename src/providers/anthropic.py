from anthropic import AsyncAnthropic
import json

from .base import LLMProvider, LLMResponse
from ..core.config import settings


class AnthropicProvider(LLMProvider):
    """Anthropic Claude LLM provider."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or settings.anthropic_api_key
        self.model_name = model or settings.anthropic_model
        self.client = AsyncAnthropic(api_key=self.api_key)

    async def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Generate a response from Anthropic Claude."""
        response = await self.client.messages.create(
            model=self.model_name,
            max_tokens=max_tokens,
            system=system_prompt or "",
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
        )

        usage = {
            "prompt_tokens": response.usage.input_tokens,
            "completion_tokens": response.usage.output_tokens,
            "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
        }

        return LLMResponse(
            content=response.content[0].text,
            raw_response=response,
            model=self.model_name,
            usage=usage,
        )

    async def generate_structured(
        self,
        prompt: str,
        response_schema: dict,
        system_prompt: str | None = None,
        temperature: float = 0.3,
    ) -> dict:
        """Generate a structured JSON response from Anthropic Claude."""
        schema_str = json.dumps(response_schema, indent=2)
        structured_prompt = f"""{prompt}

Respond with valid JSON matching this schema:
{schema_str}

Return ONLY the JSON, no additional text."""

        response = await self.generate(
            prompt=structured_prompt,
            system_prompt=system_prompt,
            temperature=temperature,
        )

        content = response.content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]

        return json.loads(content.strip())

    def get_model_name(self) -> str:
        return self.model_name
