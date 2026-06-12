"""End-to-end: scrape (offline) -> tailor (stub/echo) -> compile (if engine).

This is the regression guard for the whole pipeline wiring."""
import shutil

import pytest

from resume_tailor.compiler import compile_tex
from resume_tailor.latex import extract_regions
from resume_tailor.llm import get_provider
from resume_tailor.scraper import scrape
from resume_tailor.tailor import tailor_tex

from resume_tailor.compiler import has_engine
_HAS_ENGINE = has_engine()

_JOB_HTML = """
<html><head><script type="application/ld+json">
{"@type":"JobPosting","title":"Backend Engineer",
"hiringOrganization":{"name":"Globex"},
"description":"Go, Python, distributed systems, reliability."}
</script></head><body></body></html>
"""


def test_pipeline_scrape_to_tex(examples_dir):
    job = scrape("http://x", html_text=_JOB_HTML)
    assert job.title == "Backend Engineer"

    tex = (examples_dir / "sample_resume.tex").read_text()
    regions = extract_regions(tex)
    assert len(regions) > 0  # at least one tailorable region

    prov = get_provider("echo")
    new_tex, report = tailor_tex(tex, job, prov)
    # echo returns unchanged bodies -> output equals input, structure intact
    assert new_tex.count("\\begin{itemize}") == tex.count("\\begin{itemize}")
    assert new_tex.count("\\end{itemize}") == tex.count("\\end{itemize}")
    assert "\\documentclass" in new_tex
    assert report["regions_found"] > 0


@pytest.mark.skipif(not _HAS_ENGINE, reason="no LaTeX engine installed")
def test_pipeline_compiles_real_resume(examples_dir, tmp_path):
    job = scrape("http://x", html_text=_JOB_HTML)
    tex = (examples_dir / "sample_resume.tex").read_text()
    new_tex, _ = tailor_tex(tex, job, get_provider("echo"))
    out = tmp_path / "tailored.tex"
    out.write_text(new_tex)
    pdf = compile_tex(out, tmp_path)
    assert pdf.exists() and pdf.stat().st_size > 10_000  # real 1-page resume
