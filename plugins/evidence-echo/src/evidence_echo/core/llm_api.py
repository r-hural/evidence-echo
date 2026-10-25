"""Anthropic API implementation of LLMBackend."""
import anthropic

DEFAULT_MODEL = "claude-haiku-4-5-20251001"


class AnthropicBackend:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        if not api_key:
            raise ValueError(
                "the api backend needs an Anthropic API key: set it in the plugin "
                "settings, or use the session backend")
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def complete(self, prompt: str, max_tokens: int = 500) -> str:
        msg = self._client.messages.create(
            model=self._model, max_tokens=max_tokens, temperature=0,
            messages=[{"role": "user", "content": prompt}])
        return msg.content[0].text
