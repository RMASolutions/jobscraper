import google.generativeai as genai
from google.generativeai.types import GenerationConfig
import json

from .base import LLMProvider, LLMResponse
from ..core.config import settings


class GeminiProvider(LLMProvider):
    """Google Gemini LLM provider."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or settings.gemini_api_key
        self.model_name = model or settings.gemini_model
        genai.configure(api_key=self.api_key)
        self.model = genai.GenerativeModel(self.model_name)

    async def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Generate a response from Gemini."""
        generation_config = GenerationConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
        )

        full_prompt = prompt
        if system_prompt:
            full_prompt = f"{system_prompt}\n\n{prompt}"

        response = await self.model.generate_content_async(
            full_prompt,
            generation_config=generation_config,
        )

        usage = None
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            usage = {
                "prompt_tokens": response.usage_metadata.prompt_token_count,
                "completion_tokens": response.usage_metadata.candidates_token_count,
                "total_tokens": response.usage_metadata.total_token_count,
            }

        return LLMResponse(
            content=response.text,
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
        """Generate a structured JSON response from Gemini."""
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

        # Parse JSON from response
        content = response.content.strip()
        # Remove markdown code blocks if present
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]

        return json.loads(content.strip())

    def get_model_name(self) -> str:
        return self.model_name
