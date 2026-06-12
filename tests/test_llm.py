"""LLM provider registry + echo provider + proxy helper."""
import os

import pytest

from resume_tailor.llm import get_provider
from resume_tailor.llm.base import LLMProvider, proxy_from_env


def test_get_echo_provider():
    p = get_provider("echo")
    assert p.name == "echo"
    assert isinstance(p, LLMProvider)


def test_get_provider_unknown_raises():
    with pytest.raises(ValueError):
        get_provider("nonexistent-backend")


def test_get_provider_defaults_to_env(monkeypatch):
    monkeypatch.setenv("RESUME_TAILOR_PROVIDER", "echo")
    p = get_provider(None)
    assert p.name == "echo"


def test_gemini_default_model_is_free_tier(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    p = get_provider("gemini")
    # default must remain a free-tier-capable model
    assert p.model == "gemini-flash-lite-latest"


def test_gemini_model_override(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    p = get_provider("gemini", model="gemini-2.5-pro")
    assert p.model == "gemini-2.5-pro"


def test_gemini_env_model_override(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    p = get_provider("gemini")
    assert p.model == "gemini-2.5-flash"


def test_gemini_requires_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        get_provider("gemini")


def test_echo_provider_returns_valid_edits_json():
    import json
    p = get_provider("echo")
    user = 'regions: "marker:summary" REGIONS_JSON: {"marker:summary": "text"}'
    out = json.loads(p.complete("sys", user))
    assert "edits" in out
    assert out["edits"]["marker:summary"] == "text"


def test_proxy_from_env(monkeypatch):
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    assert proxy_from_env() is None
    monkeypatch.setenv("https_proxy", "http://fwdproxy:8080")
    assert proxy_from_env() == "http://fwdproxy:8080"


def test_get_ollama_provider():
    p = get_provider("ollama")
    assert p.name == "ollama"
    assert p.model  # has a default model
    assert p.host.startswith("http")


def test_ollama_model_and_host_override(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5")
    monkeypatch.setenv("OLLAMA_HOST", "http://192.168.1.5:11434/")
    p = get_provider("ollama")
    assert p.model == "qwen2.5"
    assert p.host == "http://192.168.1.5:11434"  # trailing slash stripped


def test_ollama_connection_error_is_friendly(monkeypatch):
    import httpx
    from resume_tailor.llm.ollama import OllamaProvider

    p = OllamaProvider(host="http://localhost:59999")  # nothing listening

    def boom(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx.Client, "post", boom)
    import pytest
    with pytest.raises(RuntimeError, match="Ollama not reachable"):
        p.complete("sys", "user")


def test_quota_detail_parses_limit_and_retry():
    from resume_tailor.llm.gemini import _quota_detail

    class R:
        def json(self):
            return {"error": {"message":
                    "Quota exceeded ... limit: 250, model: x. Please retry in 31.5s."}}
    d = _quota_detail(R())
    assert d["limit"] == 250
    assert abs(d["retry"] - 31.5) < 0.01


def test_gemini_limit_zero_fails_fast_no_retry(monkeypatch):
    import httpx
    from resume_tailor.llm.gemini import GeminiProvider

    p = GeminiProvider(model="gemini-2.5-pro", api_key="fake")

    class Resp:
        status_code = 429
        def json(self):
            return {"error": {"message": "Quota ... limit: 0, model: gemini-2.5-pro."}}

    calls = {"n": 0}
    def fake_post(self, url, **k):
        calls["n"] += 1
        return Resp()

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    import pytest
    with pytest.raises(RuntimeError, match="not available on the free tier"):
        p.complete("s", "u")
    assert calls["n"] == 1  # fast-fail: no retry loop on limit:0
