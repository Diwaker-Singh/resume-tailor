"""End-to-end CLI tests — the orchestration layer that wires config, scrape,
tailor, and compile together. Uses the offline `echo` provider and a local
HTML fixture so no network/LLM/key is needed; PDF steps use --no-pdf so these
run without a LaTeX engine."""
import textwrap

import pytest

from resume_tailor import cli

_JOB_HTML = (
    "<html><head><title>Backend Engineer at Globex</title></head>"
    "<body><main>" + ("Build distributed systems in Python. " * 20)
    + "</main></body></html>"
)


@pytest.fixture(autouse=True)
def offline_scrape(monkeypatch):
    """Never hit the network: feed every scrape() call a local HTML fixture."""
    import resume_tailor.cli as climod

    orig = climod.scrape
    monkeypatch.setattr(climod, "scrape", lambda url: orig(url, html_text=_JOB_HTML))


@pytest.fixture
def resume_file(tmp_path):
    r = tmp_path / "resume.tex"
    r.write_text(
        "\\documentclass{article}\\begin{document}\n"
        "% <TAILOR:summary>\nOld summary.\n% </TAILOR>\n"
        "\\end{document}\n"
    )
    return r


@pytest.fixture
def config_file(tmp_path, resume_file):
    out = tmp_path / "out"
    cfg = tmp_path / "rt.toml"
    cfg.write_text(textwrap.dedent(f"""
        [paths]
        resume = "{resume_file}"
        output_dir = "{out}"
        [output]
        per_job_subdir = false
        [llm]
        provider = "echo"
    """))
    return cfg


def _run(monkeypatch, args):
    # echo provider needs no key; make sure no real provider env leaks in
    monkeypatch.delenv("RESUME_TAILOR_PROVIDER", raising=False)
    monkeypatch.delenv("RESUME_TAILOR_DEFAULT_RESUME", raising=False)
    return cli.main(args)


def test_cli_runs_with_config_no_pdf(monkeypatch, tmp_path, config_file, resume_file):
    out = tmp_path / "out"
    rc = _run(monkeypatch, [
        "http://example.com/job", "--config", str(config_file),
        "--provider", "echo", "--no-pdf",
    ])
    assert rc == 0
    tailored = out / "resume_tailored.tex"
    assert tailored.exists()
    # structure preserved, markers still present
    txt = tailored.read_text()
    assert "\\documentclass" in txt and "TAILOR:summary" in txt


def test_cli_flag_overrides_config_resume(monkeypatch, tmp_path, config_file):
    # a resume passed on the CLI should win over the config's resume
    alt = tmp_path / "alt.tex"
    alt.write_text("\\documentclass{article}\\begin{document}"
                   "% <TAILOR:x>\nhi\n% </TAILOR>\\end{document}")
    out = tmp_path / "cliout"
    rc = _run(monkeypatch, [
        "http://example.com/job", "--config", str(config_file),
        "--resume", str(alt), "--out-dir", str(out),
        "--provider", "echo", "--no-pdf",
    ])
    assert rc == 0
    assert (out / "alt_tailored.tex").exists()


def test_cli_per_job_subdir(monkeypatch, tmp_path, resume_file):
    out = tmp_path / "out"
    cfg = tmp_path / "rt.toml"
    cfg.write_text(textwrap.dedent(f"""
        [paths]
        resume = "{resume_file}"
        output_dir = "{out}"
        [output]
        per_job_subdir = true
        subdir_template = "{{company}}-{{title}}"
        [llm]
        provider = "echo"
    """))
    # offline_scrape (autouse) feeds the "Backend Engineer at Globex" fixture
    rc = _run(monkeypatch, [
        "http://x/job", "--config", str(cfg), "--provider", "echo", "--no-pdf",
    ])
    assert rc == 0
    # a per-job subdir should have been created under out/
    subdirs = [p for p in out.iterdir() if p.is_dir()]
    assert len(subdirs) == 1
    assert "globex" in subdirs[0].name and "backend-engineer" in subdirs[0].name


def test_cli_missing_resume_errors(monkeypatch, tmp_path):
    cfg = tmp_path / "empty.toml"
    cfg.write_text("[paths]\noutput_dir = \"%s\"\n" % (tmp_path / "o"))
    with pytest.raises(SystemExit):   # argparse p.error exits
        _run(monkeypatch, ["http://x/job", "--config", str(cfg), "--provider", "echo"])


def test_cli_nonexistent_resume_errors(monkeypatch, tmp_path):
    with pytest.raises(SystemExit):
        _run(monkeypatch, [
            "http://x/job", "--resume", str(tmp_path / "nope.tex"),
            "--provider", "echo",
        ])


def test_cli_writes_job_json(monkeypatch, tmp_path, config_file):
    import json as _json
    out = tmp_path / "out"
    rc = _run(monkeypatch, [
        "http://example.com/job", "--config", str(config_file),
        "--provider", "echo", "--no-pdf",
    ])
    assert rc == 0
    jj = out / "job.json"
    assert jj.exists()
    data = _json.loads(jj.read_text())
    # fixture is the "Backend Engineer at Globex" page (text-fallback)
    assert data["source"] in ("json-ld", "text-fallback")
    assert "description" in data and "fields_found" in data
    assert data["url"] == "http://example.com/job"


def test_cli_compaction_loop_retries_until_fits(monkeypatch, tmp_path, resume_file):
    """When the first compile overflows, the CLI should re-tailor (shorter) and
    succeed once it fits — without --allow-overflow."""
    import textwrap
    import resume_tailor.cli as climod

    out = tmp_path / "out"
    cfg = tmp_path / "rt.toml"
    cfg.write_text(textwrap.dedent(f"""
        [paths]
        resume = "{resume_file}"
        output_dir = "{out}"
        [output]
        per_job_subdir = false
        max_compaction_passes = 3
        [llm]
        provider = "echo"
    """))

    # Fake compile: just create the pdf file; page count is driven below.
    def fake_compile(tex_path, out_dir):
        from pathlib import Path
        p = Path(out_dir) / (Path(tex_path).stem + ".pdf")
        p.write_bytes(b"%PDF-1.4 fake")
        return p
    monkeypatch.setattr(climod, "compile_tex", fake_compile)

    # First measurement = 2 pages (overflow), second = 1 page (fits).
    pages_seq = iter([2, 1])
    monkeypatch.setattr(climod, "page_count", lambda *a, **k: next(pages_seq))
    monkeypatch.setattr(climod, "vertical_overflow", lambda *a, **k: 0.0)

    # count how many times we re-tailored
    calls = {"n": 0}
    real_tailor = climod.tailor_tex
    def counting_tailor(tex, job, provider, shorten=0, **kwargs):
        calls["n"] += 1
        return real_tailor(tex, job, provider, shorten=shorten)
    monkeypatch.setattr(climod, "tailor_tex", counting_tailor)

    rc = _run(monkeypatch, ["http://x/job", "--config", str(cfg), "--provider", "echo"])
    assert rc == 0          # succeeded after compaction
    assert calls["n"] == 2  # one normal pass + one shorten pass


def test_cli_compaction_exhausts_then_errors(monkeypatch, tmp_path, resume_file):
    """If it never fits within max_compaction_passes, exit 3 (unless allowed)."""
    import textwrap
    import resume_tailor.cli as climod

    out = tmp_path / "out"
    cfg = tmp_path / "rt.toml"
    cfg.write_text(textwrap.dedent(f"""
        [paths]
        resume = "{resume_file}"
        output_dir = "{out}"
        [output]
        max_compaction_passes = 2
        [llm]
        provider = "echo"
    """))

    def fake_compile(tex_path, out_dir):
        from pathlib import Path
        p = Path(out_dir) / (Path(tex_path).stem + ".pdf")
        p.write_bytes(b"%PDF-1.4 fake")
        return p
    monkeypatch.setattr(climod, "compile_tex", fake_compile)
    monkeypatch.setattr(climod, "page_count", lambda *a, **k: 2)   # always overflows
    monkeypatch.setattr(climod, "vertical_overflow", lambda *a, **k: 0.0)

    rc = _run(monkeypatch, ["http://x/job", "--config", str(cfg), "--provider", "echo"])
    assert rc == 3   # one-page constraint failed

    # but --allow-overflow accepts it
    rc2 = _run(monkeypatch, [
        "http://x/job", "--config", str(cfg), "--provider", "echo", "--allow-overflow",
    ])
    assert rc2 == 0


def test_cli_loads_and_passes_profile_context(monkeypatch, tmp_path, resume_file):
    """End-to-end: a [profile] context_dir is read and its content reaches the
    LLM prompt (verified via a capturing provider injected through get_provider)."""
    # build a profile dir with one achievement file
    prof = tmp_path / "profile"
    prof.mkdir()
    (prof / "01_extra.md").write_text("EXTRA-ACHIEVEMENT: shipped a 27-diff stack.")

    out = tmp_path / "out"
    cfg = tmp_path / "rt.toml"
    cfg.write_text(
        f'[paths]\nresume = "{resume_file}"\noutput_dir = "{out}"\n'
        f'[output]\nper_job_subdir = false\n'
        f'[llm]\nprovider = "echo"\n'
        f'[profile]\ncontext_dir = "{prof}"\nmax_chars = 5000\n'
    )

    captured = {}
    import resume_tailor.cli as climod
    import json as _json
    import re as _re

    class CaptureEcho:
        name = "capture"
        def complete(self, system, user):
            captured["user"] = user
            # echo regions back UNCHANGED so >=1 edit applies (rc 0, not the
            # no-edits skip path). Parse region ids from the embedded payload.
            m = _re.search(r"REGIONS_JSON:\s*(\{.*\})\s*$", user, _re.DOTALL)
            payload = _json.loads(m.group(1)) if m else {}
            edits = {rid: v.get("text", "") if isinstance(v, dict) else v
                     for rid, v in payload.items()}
            return _json.dumps({"edits": edits})

    monkeypatch.setattr(climod, "get_provider", lambda *a, **k: CaptureEcho())
    rc = _run(monkeypatch, ["http://x/job", "--config", str(cfg), "--no-pdf"])
    assert rc == 0
    assert "EXTRA-ACHIEVEMENT" in captured["user"]
    assert "CANDIDATE PROFILE" in captured["user"]


def test_backup_previous_keeps_one_prior_version(tmp_path):
    from resume_tailor.cli import _backup_previous
    out = tmp_path
    stem = "Resume_tailored"
    # simulate a previous run's outputs
    (out / f"{stem}.tex").write_text("v1 tex")
    (out / f"{stem}.pdf").write_bytes(b"v1pdf")
    (out / "job.json").write_text('{"v":1}')

    backed = _backup_previous(out, stem)
    assert set(backed) == {f"{stem}.tex.bak", f"{stem}.pdf.bak", "job.json.bak"}
    assert (out / f"{stem}.tex.bak").read_text() == "v1 tex"
    # originals moved away
    assert not (out / f"{stem}.tex").exists()


def test_backup_replaces_older_bak(tmp_path):
    from resume_tailor.cli import _backup_previous
    out = tmp_path
    stem = "Resume_tailored"
    # an old .bak already exists from an earlier run
    (out / f"{stem}.tex.bak").write_text("OLD bak")
    (out / f"{stem}.tex").write_text("current")
    _backup_previous(out, stem)
    # the current file becomes the (single) .bak, old one is gone
    assert (out / f"{stem}.tex.bak").read_text() == "current"


def test_backup_noop_when_nothing_exists(tmp_path):
    from resume_tailor.cli import _backup_previous
    assert _backup_previous(tmp_path, "Resume_tailored") == []


def test_cli_creates_bak_on_second_run(monkeypatch, tmp_path, config_file):
    # first run writes outputs; second run should back them up
    out = tmp_path / "out"
    args = ["http://example.com/job", "--config", str(config_file),
            "--provider", "echo", "--no-pdf"]
    assert _run(monkeypatch, args) == 0
    first = (out / "Resume_tailored.tex")
    assert first.exists()
    first.write_text("MANUALLY MARKED v1")   # mark it so we can detect the .bak
    assert _run(monkeypatch, args) == 0
    assert (out / "Resume_tailored.tex.bak").read_text() == "MANUALLY MARKED v1"
    assert (out / "Resume_tailored.tex").exists()  # fresh v2 written


def test_cli_no_backup_flag(monkeypatch, tmp_path, config_file):
    out = tmp_path / "out"
    args = ["http://example.com/job", "--config", str(config_file),
            "--provider", "echo", "--no-pdf", "--no-backup"]
    assert _run(monkeypatch, args) == 0
    (out / "Resume_tailored.tex").write_text("v1")
    assert _run(monkeypatch, args) == 0
    assert not (out / "Resume_tailored.tex.bak").exists()  # no backup made


def test_write_diff_produces_unified_diff(tmp_path):
    from resume_tailor.cli import _write_diff
    src = "line one\nold summary\nline three\n"
    new = "line one\nNEW tailored summary\nline three\n"
    dest = tmp_path / "out.diff"
    _write_diff(src, new, "resume.tex", "tailored.tex", dest)
    text = dest.read_text()
    assert "--- resume.tex" in text and "+++ tailored.tex" in text
    assert "-old summary" in text
    assert "+NEW tailored summary" in text


def test_diff_hint_returns_command_or_none(monkeypatch, tmp_path):
    from resume_tailor.cli import _diff_hint
    import resume_tailor.cli as climod
    left, right = tmp_path / "a.tex", tmp_path / "b.tex"

    # when an editor CLI exists -> a command string
    monkeypatch.setattr(climod.shutil, "which",
                        lambda c: "/usr/local/bin/code" if c == "code" else None)
    hint = _diff_hint(left, right)
    assert hint and "code --diff" in hint and str(left) in hint

    # when none exist -> None (no auto-launch, graceful)
    monkeypatch.setattr(climod.shutil, "which", lambda c: None)
    assert _diff_hint(left, right) is None


def test_cli_writes_diff_file(monkeypatch, tmp_path, config_file):
    out = tmp_path / "out"
    rc = _run(monkeypatch, ["http://example.com/job", "--config", str(config_file),
                            "--provider", "echo", "--no-pdf"])
    assert rc == 0
    diffs = list(out.glob("*.diff"))
    assert len(diffs) == 1
    # echo provider returns regions unchanged, so the diff is valid (possibly empty body)
    assert diffs[0].name.endswith("_tailored.diff")


class _FailingProvider:
    name = "failing"
    model = "x"
    def complete(self, system, user):
        raise RuntimeError("Gemini quota exhausted for model 'x'")


class _NoEditProvider:
    """Returns valid JSON with no edits (simulates LLM that changed nothing)."""
    name = "noedit"
    model = "x"
    def complete(self, system, user):
        return '{"edits": {}}'


def test_cli_aborts_cleanly_on_provider_failure(monkeypatch, tmp_path, config_file):
    import resume_tailor.cli as climod
    monkeypatch.setattr(climod, "get_provider", lambda *a, **k: _FailingProvider())
    rc = _run(monkeypatch, ["http://example.com/job", "--config", str(config_file),
                            "--no-pdf"])
    assert rc == 4  # provider failure exit code


def test_cli_skips_pdf_when_no_edits(monkeypatch, tmp_path, config_file):
    import resume_tailor.cli as climod
    monkeypatch.setattr(climod, "get_provider", lambda *a, **k: _NoEditProvider())
    out = tmp_path / "out"
    # NOTE: not --no-pdf — we want to prove the PDF is skipped on its own
    rc = _run(monkeypatch, ["http://example.com/job", "--config", str(config_file)])
    assert rc == 5  # no-edits exit code
    assert not list(out.glob("*.pdf"))   # PDF was skipped
    assert list(out.glob("*.tex"))       # unchanged .tex still written for transparency
