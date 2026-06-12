"""Job scraping: JSON-LD extraction + fallback, offline via html_text."""
from resume_tailor.scraper import JobPosting, scrape

_JSON_LD_HTML = """
<html><head><title>Senior Backend Engineer at Globex</title>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"JobPosting",
"title":"Senior Backend Engineer",
"hiringOrganization":{"@type":"Organization","name":"Globex"},
"jobLocation":{"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"Remote"}},
"description":"<p>Design distributed systems in Go and Python. Own reliability, scale data pipelines, mentor engineers. Requirements: 5+ years backend, Kubernetes.</p>"}
</script></head><body><main>fallback text</main></body></html>
"""

_NO_JSONLD_HTML = """
<html><head><title>Data Scientist - Acme</title></head>
<body><main>We are hiring a Data Scientist to build ML models in Python.
You will work with large datasets and deploy models to production. This is a
long description with plenty of content so the fallback path is exercised fully
and exceeds the minimum length threshold used by the scraper implementation.
""" + ("More detail. " * 40) + "</main></body></html>"


def test_json_ld_extraction_fields():
    job = scrape("http://x/job", html_text=_JSON_LD_HTML)
    assert job.title == "Senior Backend Engineer"
    assert job.company == "Globex"
    assert job.location == "Remote"
    assert "distributed systems" in job.description.lower()
    # html inside description is stripped
    assert "<p>" not in job.description


def test_fallback_when_no_json_ld():
    job = scrape("http://x/job", html_text=_NO_JSONLD_HTML)
    assert "Data Scientist" in job.title
    assert "ml models" in job.description.lower()


def test_malformed_json_ld_does_not_crash():
    html = (
        '<html><body><script type="application/ld+json">'
        '{bad json,,}</script><main>Real content here for fallback. '
        + ("padding " * 50) + "</main></body></html>"
    )
    job = scrape("http://x/job", html_text=html)
    assert "Real content" in job.description


def test_as_prompt_context_includes_fields_and_truncates():
    job = JobPosting(
        url="u", title="T", company="C", location="L",
        description="x" * 20000,
    )
    ctx = job.as_prompt_context(max_chars=500)
    assert "Job Title: T" in ctx
    assert "Company: C" in ctx
    assert "truncated" in ctx
    assert len(ctx) < 1000


def test_as_prompt_context_omits_empty_fields():
    job = JobPosting(url="u", title="T", description="d")
    ctx = job.as_prompt_context()
    assert "Company:" not in ctx
    assert "Location:" not in ctx


def test_title_company_split_from_boilerplate():
    html = (
        "<html><head><title>Job Application for Software Engineer, Safeguards "
        "at Anthropic</title></head><body><main>" + ("desc " * 60)
        + "</main></body></html>"
    )
    job = scrape("http://x/job", html_text=html)
    assert job.title == "Software Engineer, Safeguards"
    assert job.company == "Anthropic"


def test_title_prefix_stripped_without_company():
    html = (
        "<html><head><title>Job Application for Staff Engineer</title></head>"
        "<body><main>" + ("desc " * 60) + "</main></body></html>"
    )
    job = scrape("http://x/job", html_text=html)
    assert job.title == "Staff Engineer"


def test_company_backfill_from_og_site_name():
    html = (
        '<html><head><title>Backend Engineer at </title>'
        '<meta property="og:site_name" content="Globex Corp"></head>'
        "<body><main>" + ("desc " * 60) + "</main></body></html>"
    )
    job = scrape("http://x/job", html_text=html)
    assert job.company == "Globex Corp"


def test_as_dict_marks_json_ld_source():
    job = scrape("http://x/job", html_text=_JSON_LD_HTML)
    d = job.as_dict()
    assert d["source"] == "json-ld"
    assert d["fields_found"]["json_ld"] is True
    assert d["fields_found"]["title"] is True
    assert d["description_chars"] == len(job.description)
    assert d["url"] == "http://x/job"


def test_as_dict_marks_text_fallback_source():
    job = scrape("http://x/job", html_text=_NO_JSONLD_HTML)
    d = job.as_dict()
    assert d["source"] == "text-fallback"
    assert d["fields_found"]["json_ld"] is False


# ---- JD prioritisation before truncation ---------------------------------

from resume_tailor.scraper import prioritize_jd  # noqa: E402


def test_prioritize_keeps_skills_and_requirements_over_boilerplate():
    jd = (
        "About Us\n" + ("We are a great company. " * 80) + "\n\n"
        "Benefits\n" + ("Free lunch and gym membership. " * 80) + "\n\n"
        "Required Skills\nPython, Go, Kubernetes, distributed systems.\n\n"
        "Requirements\n5+ years backend experience, strong testing culture."
    )
    out = prioritize_jd(jd, 500)
    # high-value sections survive even though they were at the bottom
    assert "Python, Go, Kubernetes" in out
    assert "5+ years backend" in out
    # boilerplate that was at the top is dropped
    assert "Free lunch" not in out


def test_prioritize_noop_when_under_budget():
    jd = "Short JD with skills: Python and Go."
    assert prioritize_jd(jd, 8000) == jd


def test_prioritize_skills_ranked_before_role():
    jd = (
        "The Role\n" + ("You will build and own services. " * 60) + "\n\n"
        "Skills\nRust, gRPC, observability, Postgres."
    )
    out = prioritize_jd(jd, 220)
    # skills (tier 1) kept before role (tier 3) under tight budget
    assert "Rust, gRPC" in out


def test_prioritize_handles_empty():
    assert prioritize_jd("", 100) == ""


def test_prioritize_segments_jd_without_blank_lines():
    # Real-world text-fallback scrapes often have NO blank lines, only heading
    # lines. The splitter must still segment on headings so reordering works.
    jd = (
        "About Us\n" + "We build great things. " * 50 + "\n"
        "Benefits\n" + "Free lunch every day. " * 50 + "\n"
        "Requirements:\n5+ years backend, Python, Kubernetes, distributed systems.\n"
        "Responsibilities:\nYou will own services end to end."
    )
    out = prioritize_jd(jd, 400)
    assert "5+ years backend" in out          # requirements kept
    assert "Free lunch" not in out            # boilerplate dropped
