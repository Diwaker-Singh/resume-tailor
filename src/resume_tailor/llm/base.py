"""LLM provider interface + registry."""
from __future__ import annotations

import abc
import os


def proxy_from_env() -> str | None:
    """HTTPS/HTTP proxy from env (fwdproxy-friendly; avoids httpx NO_PROXY bug)."""
    return (
        os.environ.get("https_proxy")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("http_proxy")
        or os.environ.get("HTTP_PROXY")
    )


class LLMProvider(abc.ABC):
    """Implement this to plug in any backend."""

    name: str = "base"

    @abc.abstractmethod
    def complete(self, system: str, user: str) -> str:
        """Return the model's text completion."""
        raise NotImplementedError


def get_provider(name: str | None = None, **kwargs) -> "LLMProvider":
    """Factory. name defaults to $RESUME_TAILOR_PROVIDER or 'gemini'."""
    name = (name or os.environ.get("RESUME_TAILOR_PROVIDER") or "gemini").lower()
    if name == "gemini":
        from .gemini import GeminiProvider
        return GeminiProvider(**kwargs)
    if name == "openai":
        from .openai_provider import OpenAIProvider
        return OpenAIProvider(**kwargs)
    if name == "ollama":
        from .ollama import OllamaProvider
        return OllamaProvider(**kwargs)
    if name == "echo":
        from .echo import EchoProvider
        return EchoProvider(**kwargs)
    raise ValueError(f"unknown provider: {name}")
