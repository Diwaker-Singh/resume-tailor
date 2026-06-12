"""Ollama provider — run a local LLM (no API key, no quota, fully private).

Setup:
    brew install ollama          # or https://ollama.com/download
    ollama serve                 # starts the local server on :11434
    ollama pull llama3.1         # or qwen2.5, mistral, etc.

Then:
    export RESUME_TAILOR_PROVIDER=ollama
    export OLLAMA_MODEL=llama3.1            # optional, default below
    export OLLAMA_HOST=http://localhost:11434   # optional

Because it runs on your machine, your resume + profile data never leave it —
ideal for sensitive content. Quality depends on the local model you pull.
"""
from __future__ import annotations

import os

import httpx

from ..logging_setup import get_logger
from .base import LLMProvider

_log = get_logger("llm.ollama")
_DEFAULT_MODEL = "llama3.1"
_DEFAULT_HOST = "http://localhost:11434"


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, model: str | None = None, host: str | None = None,
                 timeout: float = 300.0):
        self.model = model or os.environ.get("OLLAMA_MODEL", _DEFAULT_MODEL)
        self.host = (host or os.environ.get("OLLAMA_HOST", _DEFAULT_HOST)).rstrip("/")
        self.timeout = timeout

    def complete(self, system: str, user: str) -> str:
        url = f"{self.host}/api/generate"
        payload = {
            "model": self.model,
            "stream": False,
            "format": "json",          # ask for strict JSON output
            "options": {"temperature": 0.4},
            "system": system,
            "prompt": user,
        }
        _log.debug("Ollama request model=%s host=%s", self.model, self.host)
        try:
            # Ollama is local => no proxy.
            with httpx.Client(timeout=self.timeout, trust_env=False) as client:
                resp = client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.ConnectError as exc:
            _log.error("Cannot reach Ollama at %s — is `ollama serve` running?", self.host)
            raise RuntimeError(
                f"Ollama not reachable at {self.host}. Start it with `ollama serve` "
                f"and ensure the model is pulled (`ollama pull {self.model}`)."
            ) from exc
        try:
            text = data["response"]
        except (KeyError, TypeError) as exc:
            _log.error("Unexpected Ollama response: %s", data)
            raise RuntimeError(f"unexpected Ollama response: {data}") from exc
        _log.debug("Ollama ok model=%s (%d chars)", self.model, len(text))
        return text
