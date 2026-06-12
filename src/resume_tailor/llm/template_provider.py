"""TEMPLATE for adding a new LLM provider — copy this file to plug in a backend.

HOW TO USE (3 steps):

  1. Copy this file to  src/resume_tailor/llm/<yourname>.py  and rename the class.
     Replace the API call in complete() with your provider's request/response.

  2. Register it in  src/resume_tailor/llm/base.py::get_provider  by adding:

         if name == "<yourname>":
             from .<yourname> import YourProvider
             return YourProvider(**kwargs)

  3. Select it:
         resume-tailor "<url>" --provider <yourname>          # one-off
         export RESUME_TAILOR_PROVIDER=<yourname>             # persistent
         export YOURNAME_API_KEY=...  YOURNAME_MODEL=...

That's it. Caching, the one-page compaction loop, JSON parsing, and the .bak
re-run logic all keep working because they only depend on complete().

CONTRACT / RULES:
  - complete(system, user) -> str : return the model's TEXT. The orchestrator
    parses JSON edits out of it, so request JSON output if your API supports it
    (it tolerates code fences and trailing prose, but clean JSON is best).
  - Honor a `model` kwarg so --model works.
  - Read secrets from ENV, never from the config file (keeps keys out of git).
  - For EXTERNAL APIs pass proxy=proxy_from_env() so it works behind a corporate
    /fwdproxy. For a LOCAL server (like Ollama) DON'T set a proxy.
  - Log via get_logger("llm.<yourname>").
  - Raise a clear RuntimeError on failure; the pipeline degrades gracefully
    (keeps the resume unchanged and records a note).
  - Add a test mirroring tests/test_llm.py::test_get_ollama_provider.

This file is NOT registered in get_provider, so it never runs — it's reference
scaffolding only.
"""
from __future__ import annotations

import os

import httpx

from ..logging_setup import get_logger
from .base import LLMProvider, proxy_from_env

_log = get_logger("llm.template")

_DEFAULT_MODEL = "your-default-model"
_API_URL = "https://api.example.com/v1/chat/completions"


class TemplateProvider(LLMProvider):
    name = "template"  # <-- the string used for --provider / RESUME_TAILOR_PROVIDER

    def __init__(self, model: str | None = None, api_key: str | None = None):
        self.model = model or os.environ.get("TEMPLATE_MODEL", _DEFAULT_MODEL)
        self.api_key = api_key or os.environ.get("TEMPLATE_API_KEY", "")
        if not self.api_key:
            raise RuntimeError(
                "TEMPLATE_API_KEY not set. Export it or pass api_key=..."
            )

    def complete(self, system: str, user: str) -> str:
        _log.debug("Template request model=%s", self.model)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # If your API supports a JSON/structured-output mode, enable it here.
        }
        # External API => honor proxy. (Local server? use trust_env=False, no proxy.)
        with httpx.Client(timeout=120, trust_env=False, proxy=proxy_from_env()) as client:
            resp = client.post(
                _API_URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        try:
            # Adapt this path to your provider's response shape.
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            _log.error("Unexpected response shape: %s", data)
            raise RuntimeError(f"unexpected response: {data}") from exc
