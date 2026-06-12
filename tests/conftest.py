"""Shared fixtures and helpers for the test suite.

The package is imported from its installed (editable) location — run
`pip install -e ".[dev]"` first. No sys.path manipulation needed.
"""
import pathlib

import pytest

from resume_tailor.llm.base import LLMProvider
from resume_tailor.scraper import JobPosting

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> pathlib.Path:
    """Directory holding test data (sample job HTML, sample resume)."""
    return FIXTURES


@pytest.fixture
def examples_dir() -> pathlib.Path:
    """User-facing examples (the real resume lives here)."""
    return ROOT / "examples"


@pytest.fixture
def sample_job() -> JobPosting:
    return JobPosting(
        url="http://example.com/job",
        title="Senior Backend Engineer",
        company="Globex",
        location="Remote",
        description="Design distributed systems in Go and Python. "
        "Own reliability, scale data pipelines, mentor engineers.",
    )


class StubProvider(LLMProvider):
    """Returns a fixed edits dict regardless of input. For deterministic tests."""

    name = "stub"

    def __init__(self, edits: dict[str, str] | None = None, raw: str | None = None):
        self._edits = edits or {}
        self._raw = raw  # if set, returned verbatim (to test parse failures)

    def complete(self, system: str, user: str) -> str:
        import json

        if self._raw is not None:
            return self._raw
        return json.dumps({"edits": self._edits})
