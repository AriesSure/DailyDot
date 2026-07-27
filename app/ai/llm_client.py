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
        """Chat with tool/function calling.

        Returns ``{"name": str, "arguments": object}`` or ``None``
        (no tool call).  Raises ``RuntimeError`` if more than one tool
        call is returned.
        """
        client = self._get_client()
        resp = client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=temperature,
        )
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return None
        if len(msg.tool_calls) > 1:
            raise RuntimeError(
                f"Expected exactly 1 tool call, got {len(msg.tool_calls)}"
            )
        return {
            "name": msg.tool_calls[0].function.name,
            "arguments": json.loads(msg.tool_calls[0].function.arguments),
        }


# Module-level singleton
_llm = None


def get_llm() -> LLMClient:
    global _llm
    if _llm is None:
        _llm = LLMClient()
    return _llm
