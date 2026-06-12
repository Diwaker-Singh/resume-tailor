"""Orchestration: regions + job + provider -> validated edits + report."""
import json

import pytest

from resume_tailor.tailor import _parse_edits, tailor_tex
from conftest import StubProvider

_TEX = (
    "\\documentclass{article}\\begin{document}\n"
    "% <TAILOR:summary>\nOld summary text.\n% </TAILOR>\n"
    "% <TAILOR:skills>\nPython, Go.\n% </TAILOR>\n"
    "\\end{document}\n"
)


def test_tailor_applies_safe_edits(sample_job):
    prov = StubProvider({
        "marker:summary": "New tailored summary.",
        "marker:skills": "Go, Python, Kubernetes.",
    })
    new_tex, report = tailor_tex(_TEX, sample_job, prov)
    assert "New tailored summary." in new_tex
    assert "Go, Python, Kubernetes." in new_tex
    assert report["regions_found"] == 2
    assert report["edits_applied"] == 2
    # structure intact
    assert new_tex.count("\\begin{document}") == 1
    assert new_tex.count("\\end{document}") == 1


def test_tailor_skips_unsafe_edit(sample_job):
    prov = StubProvider({"marker:summary": "broken {brace"})
    new_tex, report = tailor_tex(_TEX, sample_job, prov)
    assert "Old summary text." in new_tex      # unchanged
    assert any("marker:summary" in n for n in report["notes"])


def test_tailor_no_regions_returns_original(sample_job):
    plain = "\\documentclass{article}\\begin{document}hi\\end{document}"
    prov = StubProvider({"marker:x": "y"})
    new_tex, report = tailor_tex(plain, sample_job, prov)
    assert new_tex == plain
    assert report["regions_found"] == 0
    assert report["edits_applied"] == 0
    assert report["notes"]


def test_tailor_handles_non_json_llm_output(sample_job):
    prov = StubProvider(raw="this is not json at all")
    new_tex, report = tailor_tex(_TEX, sample_job, prov)
    assert new_tex == _TEX                       # untouched on parse failure
    assert any("non-JSON" in n for n in report["notes"])


def test_tailor_partial_edits(sample_job):
    prov = StubProvider({"marker:summary": "Only this one changes."})
    new_tex, report = tailor_tex(_TEX, sample_job, prov)
    assert "Only this one changes." in new_tex
    assert "Python, Go." in new_tex              # skills untouched
    assert report["edits_applied"] == 1


# ---- _parse_edits robustness ----------------------------------------------

def test_parse_edits_plain_json():
    assert _parse_edits('{"edits": {"a": "1"}}') == {"a": "1"}


def test_parse_edits_strips_code_fences():
    raw = '```json\n{"edits": {"a": "1"}}\n```'
    assert _parse_edits(raw) == {"a": "1"}


def test_parse_edits_accepts_bare_dict():
    # some models omit the "edits" wrapper
    assert _parse_edits('{"a": "1", "b": "2"}') == {"a": "1", "b": "2"}


def test_parse_edits_ignores_trailing_data():
    # model emitted valid JSON then extra prose/second object on a new line
    raw = '{"edits": {"a": "1"}}\nHere is your tailored resume.'
    assert _parse_edits(raw) == {"a": "1"}


def test_parse_edits_trailing_second_object():
    raw = '{"edits": {"a": "1"}}\n{"junk": true}'
    assert _parse_edits(raw) == {"a": "1"}


def test_parse_edits_fenced_with_trailing_text():
    raw = '```json\n{"edits": {"x": "y"}}\n```\nDone!'
    assert _parse_edits(raw) == {"x": "y"}


def test_parse_edits_raises_on_garbage():
    with pytest.raises(json.JSONDecodeError):
        _parse_edits("not json")


def test_tailor_shorten_changes_prompt(sample_job, monkeypatch):
    """Higher shorten levels must inject stronger 'make it shorter' guidance."""
    from resume_tailor import tailor as T

    captured = {}

    class CapProvider:
        name = "cap"
        def complete(self, system, user):
            captured["user"] = user
            return '{"edits": {}}'

    tex = ("\\documentclass{article}\\begin{document}\n"
           "% <TAILOR:s>\nold text\n% </TAILOR>\n\\end{document}\n")
    T.tailor_tex(tex, sample_job, CapProvider(), shorten=0)
    base = captured["user"]
    T.tailor_tex(tex, sample_job, CapProvider(), shorten=2)
    aggressive = captured["user"]
    assert "shorter" not in base.lower() or "overflow" not in base.lower()
    assert "overflow" in aggressive.lower() or "aggressive" in aggressive.lower()
    assert len(aggressive) > len(base)


def test_tailor_passes_profile_context_to_llm(sample_job):
    # capture the user prompt the provider receives
    captured = {}

    class CaptureProvider(StubProvider):
        def complete(self, system, user):
            captured["system"] = system
            captured["user"] = user
            return '{"edits": {}}'

    tex = ("\\documentclass{article}\\begin{document}\n"
           "% <TAILOR:summary>\nOld.\n% </TAILOR>\n\\end{document}\n")
    tailor_tex(tex, sample_job, CaptureProvider(),
               profile_context="SECRET-ACHIEVEMENT: led a 27-diff stack.")
    assert "SECRET-ACHIEVEMENT" in captured["user"]
    assert "CANDIDATE PROFILE" in captured["user"]


def test_prompt_includes_line_budget_and_bullet_count(sample_job):
    """The user prompt must carry max_lines + bullets per region so the LLM
    can fit within the layout budget on the first pass."""
    captured = {}

    class CaptureProvider(StubProvider):
        def complete(self, system, user):
            captured["user"] = user
            return '{"edits": {}}'

    tex = (
        "\\documentclass{article}\\begin{document}\n"
        "\\begin{itemize}\n% <TAILOR:exp>\n"
        "\\item First achievement here.\n\\item Second achievement here.\n"
        "% </TAILOR>\n\\end{itemize}\n\\end{document}\n"
    )
    tailor_tex(tex, sample_job, CaptureProvider())
    assert "max_lines" in captured["user"]
    assert "bullets" in captured["user"]
    # the prompt should instruct skills > requirements > role prioritisation
    assert "SKILLS" in captured["user"] and "REQUIREMENTS" in captured["user"]


def test_prompt_payload_is_valid_json_with_nested_shape(sample_job):
    """REGIONS_JSON must parse and each region must be a {text,max_lines,bullets}."""
    import json as _json
    import re as _re
    captured = {}

    class CaptureProvider(StubProvider):
        def complete(self, system, user):
            captured["user"] = user
            return '{"edits": {}}'

    tex = ("\\documentclass{article}\\begin{document}\n"
           "% <TAILOR:summary>\nA short summary line.\n% </TAILOR>\n"
           "\\end{document}\n")
    tailor_tex(tex, sample_job, CaptureProvider())
    m = _re.search(r"REGIONS_JSON:\s*(\{.*\})\s*$", captured["user"], _re.DOTALL)
    assert m, "REGIONS_JSON block not found"
    payload = _json.loads(m.group(1))
    region = payload["marker:summary"]
    assert set(region) >= {"text", "max_lines", "bullets"}
    assert region["max_lines"] >= 1
    assert region["bullets"] == 0   # paragraph region


def test_system_prompt_has_ats_keyword_guidance():
    from resume_tailor.tailor import _SYSTEM
    s = _SYSTEM.lower()
    assert "ats" in s or "applicant tracking" in s
    assert "keyword" in s
    assert "exact" in s  # mirror exact JD wording
    # truthfulness guard still present
    assert "never invent" in s
    # JSON escaping guidance present
    assert "escape" in s and "backslash" in s


# ---- _parse_naming extraction ---------------------------------------------

def test_parse_naming_happy_path(sample_job):
    class NamedProvider:
        name = "named"
        def complete(self, system, user):
            return '{"company": "Bloomberg LP", "job_id": "SRE-19585"}'

    from resume_tailor.tailor import _parse_naming
    company, job_id = _parse_naming(sample_job, NamedProvider())
    assert company == "Bloomberg LP"
    assert job_id == "SRE-19585"


def test_parse_naming_falls_back_on_bad_json(sample_job):
    class GarbageProvider:
        name = "garbage"
        def complete(self, system, user):
            return "this is not json"

    from resume_tailor.tailor import _parse_naming
    company, job_id = _parse_naming(sample_job, GarbageProvider())
    assert company == sample_job.company
    assert job_id == sample_job.title.split("-")[0].strip()


def test_parse_naming_falls_back_on_partial_json(sample_job):
    class PartialProvider:
        name = "partial"
        def complete(self, system, user):
            return '{"company": "Bloomberg LP"}'

    from resume_tailor.tailor import _parse_naming
    company, job_id = _parse_naming(sample_job, PartialProvider())
    assert company == "Bloomberg LP"
    assert job_id == sample_job.title.split("-")[0].strip()


def test_parse_naming_prompt_contains_url(sample_job):
    captured = {}
    class CaptureProvider:
        name = "capture"
        def complete(self, system, user):
            captured["user"] = user
            return '{"company": "x", "job_id": "y"}'

    from resume_tailor.tailor import _parse_naming
    _parse_naming(sample_job, CaptureProvider())
    assert sample_job.url in captured["user"]
    assert "Job ID" in captured["user"]
