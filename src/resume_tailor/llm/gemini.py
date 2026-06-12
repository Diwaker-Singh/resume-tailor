"""Google Gemini provider (free tier). Uses REST, honors proxy env."""
from __future__ import annotations

import os
import time

import httpx

from ..logging_setup import get_logger
from .base import LLMProvider, proxy_from_env

_log = get_logger("llm.gemini")

_DEFAULT_MODEL = "gemini-flash-lite-latest"
_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
_BACKOFF = [4, 10, 20, 40]  # seconds between 429 retries


def _quota_detail(resp) -> dict:
    """Parse a 429 response body for the quota limit and suggested retry delay.
    Returns {"limit": int|None, "retry": float|None}. Best-effort; never raises."""
    import re as _re

    out: dict = {"limit": None, "retry": None}
    try:
        msg = resp.json().get("error", {}).get("message", "")
    except Exception:
        return out
    m = _re.search(r"limit:\s*(\d+)", msg)
    if m:
        out["limit"] = int(m.group(1))
    m = _re.search(r"retry in\s*([0-9.]+)s", msg)
    if m:
        out["retry"] = float(m.group(1))
    return out


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        max_retries: int = 4,
    ):
        self.model = model or os.environ.get("GEMINI_MODEL", _DEFAULT_MODEL)
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self.max_retries = max_retries
        if not self.api_key:
            raise RuntimeError(
                "GEMINI_API_KEY not set. Get a free key at "
                "https://aistudio.google.com/app/apikey"
            )

    def complete(self, system: str, user: str) -> str:
        url = f"{_BASE}/{self.model}:generateContent?key={self.api_key}"
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": 0.4,
                "responseMimeType": "application/json",
            },
        }
        last_exc: Exception | None = None
        with httpx.Client(timeout=120, trust_env=False, proxy=proxy_from_env()) as client:
            for attempt in range(self.max_retries):
                _log.debug("Gemini request model=%s attempt=%d/%d",
                           self.model, attempt + 1, self.max_retries)
                resp = client.post(url, json=payload)
                if resp.status_code == 429:
                    detail = _quota_detail(resp)
                    # limit:0 => model not on the free tier at all; retrying is futile.
                    if detail.get("limit") == 0:
                        _log.error("Gemini model %s has free-tier limit 0 (needs "
                                   "billing). Not retrying.", self.model)
                        raise RuntimeError(
                            f"Gemini model {self.model!r} is not available on the free "
                            f"tier (quota limit 0 — needs billing). Switch model, e.g. "
                            f"`export GEMINI_MODEL=gemini-flash-lite-latest`, or use "
                            f"`--provider ollama` to run locally."
                        )
                    wait = detail.get("retry") or float(resp.headers.get("retry-after", 0)) \
                        or _BACKOFF[min(attempt, len(_BACKOFF) - 1)]
                    _log.warning("Gemini 429 rate-limited (model=%s, limit=%s/day, "
                                 "attempt %d/%d); retrying in %.0fs", self.model,
                                 detail.get("limit", "?"), attempt + 1,
                                 self.max_retries, wait)
                    last_exc = RuntimeError(
                        f"429 rate limited (limit {detail.get('limit', '?')}/day)")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                try:
                    text = data["candidates"][0]["content"]["parts"][0]["text"]
                    _log.debug("Gemini ok model=%s (%d chars)", self.model, len(text))
                    return text
                except (KeyError, IndexError) as exc:
                    _log.error("Unexpected Gemini response shape: %s", data)
                    raise RuntimeError(f"unexpected Gemini response: {data}") from exc
        _log.error("Gemini exhausted %d retries (model=%s) — free-tier quota likely "
                   "exhausted for today", self.max_retries, self.model)
        raise RuntimeError(
            f"Gemini quota exhausted for model {self.model!r} after "
            f"{self.max_retries} retries (free-tier daily/minute limit). Try later, "
            f"set GEMINI_MODEL to another model, or use `--provider ollama`."
        ) from last_exc
