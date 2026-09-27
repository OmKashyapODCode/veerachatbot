"""
LLM provider abstraction layer.
Supports: Gemini, OpenAI, Anthropic, DeepSeek, Groq.
All providers use temperature=0 for determinism.
"""

import json
import os
import re
from abc import ABC, abstractmethod
from typing import Optional
from urllib import request as urlrequest, error as urlerror


class LLMProvider(ABC):
    @abstractmethod
    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        """Complete a prompt. Returns raw text."""
        pass

    @abstractmethod
    def name(self) -> str:
        pass


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------

class GeminiProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = ""):
        self.api_key = api_key
        self.model = model or "gemini-2.0-flash"

    def name(self) -> str:
        return f"gemini/{self.model}"

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        body = json.dumps({
            "contents": [{"parts": [{"text": full_prompt}]}],
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": 1200,
                "topP": 1.0,
                "topK": 1,
            },
        }).encode("utf-8")

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )
        req = urlrequest.Request(url, data=body, headers={"Content-Type": "application/json"})
        timeout = int(os.getenv("LLM_TIMEOUT", "20"))
        resp = urlrequest.urlopen(req, timeout=timeout)
        data = json.loads(resp.read().decode("utf-8"))
        return data["candidates"][0]["content"]["parts"][0]["text"]


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = ""):
        self.api_key = api_key
        self.model = model or "gpt-4o-mini"

    def name(self) -> str:
        return f"openai/{self.model}"

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body = json.dumps({
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": 1200,
        }).encode("utf-8")

        req = urlrequest.Request(
            "https://api.openai.com/v1/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        timeout = int(os.getenv("LLM_TIMEOUT", "20"))
        resp = urlrequest.urlopen(req, timeout=timeout)
        data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------

class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = ""):
        self.api_key = api_key
        self.model = model or "claude-3-5-haiku-20241022"

    def name(self) -> str:
        return f"anthropic/{self.model}"

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        body_dict: dict = {
            "model": self.model,
            "max_tokens": 1200,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }
        if system:
            body_dict["system"] = system

        req = urlrequest.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(body_dict).encode("utf-8"),
            headers={
                "x-api-key": self.api_key,
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            },
        )
        timeout = int(os.getenv("LLM_TIMEOUT", "20"))
        resp = urlrequest.urlopen(req, timeout=timeout)
        data = json.loads(resp.read().decode("utf-8"))
        return data["content"][0]["text"]


# ---------------------------------------------------------------------------
# DeepSeek
# ---------------------------------------------------------------------------

class DeepSeekProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = ""):
        self.api_key = api_key
        self.model = model or "deepseek-chat"

    def name(self) -> str:
        return f"deepseek/{self.model}"

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body = json.dumps({
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": 1200,
        }).encode("utf-8")

        req = urlrequest.Request(
            "https://api.deepseek.com/v1/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        timeout = int(os.getenv("LLM_TIMEOUT", "20"))
        resp = urlrequest.urlopen(req, timeout=timeout)
        data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# Groq
# ---------------------------------------------------------------------------

class GroqProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = ""):
        self.api_key = api_key
        self.model = model or "llama-3.1-70b-versatile"

    def name(self) -> str:
        return f"groq/{self.model}"

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body = json.dumps({
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": 1200,
        }).encode("utf-8")

        req = urlrequest.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        timeout = int(os.getenv("LLM_TIMEOUT", "20"))
        resp = urlrequest.urlopen(req, timeout=timeout)
        data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_llm_provider() -> Optional[LLMProvider]:
    """
    Create the configured LLM provider.
    Returns None if no API key is configured.
    """
    provider_name = os.getenv("LLM_PROVIDER", "gemini").lower()
    api_key = os.getenv("LLM_API_KEY", "")
    model = os.getenv("LLM_MODEL", "")

    if not api_key:
        return None

    providers = {
        "gemini": lambda: GeminiProvider(api_key, model),
        "openai": lambda: OpenAIProvider(api_key, model),
        "anthropic": lambda: AnthropicProvider(api_key, model),
        "deepseek": lambda: DeepSeekProvider(api_key, model),
        "groq": lambda: GroqProvider(api_key, model),
    }

    factory = providers.get(provider_name)
    if factory is None:
        return None
    return factory()


def extract_json_from_text(text: str) -> Optional[dict]:
    """Extract the first JSON object from an LLM response."""
    # Try direct parse first
    try:
        return json.loads(text.strip())
    except Exception:
        pass

    # Try extracting from code block
    match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass

    # Try finding raw JSON object
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group())
        except Exception:
            pass

    return None
