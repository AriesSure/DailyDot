"""Unified LLM client — supports OpenAI-compatible APIs (DeepSeek, Tongyi, etc.).

Configure via environment variables:
    LLM_API_KEY       — required for LLM-powered features
    LLM_BASE_URL      — defaults to https://api.deepseek.com
    LLM_MODEL         — defaults to deepseek-chat
"""

import json
import os
from openai import OpenAI


class LLMClient:
    """Thin wrapper around the OpenAI SDK for chat & function-calling."""

    def __init__(self):
        self.api_key = os.environ.get("LLM_API_KEY", "")
        self.base_url = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")
        self.model = os.environ.get("LLM_MODEL", "deepseek-chat")
        self._client = None

    @property
    def available(self) -> bool:
        """Return ``True`` when an API key has been configured."""
        return bool(self.api_key)

    def _get_client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    def chat(self, messages: list[dict], temperature: float = 0.7) -> str:
        """Simple chat completion. Returns the response text."""
        client = self._get_client()
        resp = client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
        )
        return resp.choices[0].message.content or ""

    def chat_with_tools(self, messages: list[dict], tools: list[dict],
                        temperature: float = 0.3) -> dict | None:
        """Chat with tool/function calling. Returns the first tool-call args dict."""
        client = self._get_client()
        resp = client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=temperature,
        )
        msg = resp.choices[0].message
        if msg.tool_calls:
            return json.loads(msg.tool_calls[0].function.arguments)
        return None


# Module-level singleton
_llm = None


def get_llm() -> LLMClient:
    global _llm
    if _llm is None:
        _llm = LLMClient()
    return _llm
