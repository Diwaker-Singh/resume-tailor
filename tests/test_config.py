"""Config loading + precedence (defaults <- file <- env)."""
import textwrap

from resume_tailor.config import load, slugify


def _write(tmp_path, body):
    p = tmp_path / "resume-tailor.toml"
    p.write_text(textwrap.dedent(body))
    return p


def test_defaults_when_no_file(tmp_path, monkeypatch):
    for v in ("RESUME_TAILOR_DEFAULT_RESUME", "RESUME_TAILOR_OUTPUT_DIR",
              "RESUME_TAILOR_PROVIDER", "RESUME_TAILOR_MODEL", "RESUME_TAILOR_CONFIG"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.chdir(tmp_path)
    cfg = load()
    assert cfg.output_dir == "./out"
    assert cfg.provider == "gemini"
    assert cfg.per_job_subdir is False
    assert cfg.source_path is None


def test_loads_from_file(tmp_path):
    p = _write(tmp_path, """
        [paths]
        resume = "/tmp/r.tex"
        output_dir = "/tmp/out"
        [output]
        per_job_subdir = true
        subdir_template = "{company}_{title}"
        [llm]
        provider = "openai"
        model = "gpt-4o-mini"
    """)
    cfg = load(str(p))
    assert cfg.resume == "/tmp/r.tex"
    assert cfg.output_dir == "/tmp/out"
    assert cfg.per_job_subdir is True
    assert cfg.subdir_template == "{company}_{title}"
    assert cfg.provider == "openai"
    assert cfg.model == "gpt-4o-mini"
    assert cfg.source_path == p


def test_env_overrides_file(tmp_path, monkeypatch):
    p = _write(tmp_path, """
        [paths]
        resume = "/tmp/from_file.tex"
        [llm]
        provider = "gemini"
    """)
    monkeypatch.setenv("RESUME_TAILOR_DEFAULT_RESUME", "/tmp/from_env.tex")
    monkeypatch.setenv("RESUME_TAILOR_PROVIDER", "echo")
    cfg = load(str(p))
    assert cfg.resume == "/tmp/from_env.tex"   # env wins
    assert cfg.provider == "echo"


def test_explicit_config_env_var(tmp_path, monkeypatch):
    p = _write(tmp_path, """
        [paths]
        output_dir = "/tmp/via_env_cfg"
    """)
    monkeypatch.setenv("RESUME_TAILOR_CONFIG", str(p))
    cfg = load()
    assert cfg.output_dir == "/tmp/via_env_cfg"
    assert cfg.source_path == p


def test_slugify():
    assert slugify("Anthropic") == "anthropic"
    assert slugify("Software Engineer, Safeguards") == "software-engineer-safeguards"
    assert slugify("") == "job"
    assert slugify("", "company") == "company"
    assert slugify("A/B & C++ Dev!") == "ab-c-dev"


def test_max_compaction_passes_default_and_override(tmp_path, monkeypatch):
    import textwrap
    monkeypatch.delenv("RESUME_TAILOR_MAX_COMPACTION_PASSES", raising=False)
    monkeypatch.chdir(tmp_path)
    assert load().max_compaction_passes == 4   # built-in default
    cfg = tmp_path / "rt.toml"
    cfg.write_text(textwrap.dedent("""
        [output]
        max_compaction_passes = 7
    """))
    assert load(str(cfg)).max_compaction_passes == 7
    monkeypatch.setenv("RESUME_TAILOR_MAX_COMPACTION_PASSES", "2")
    assert load(str(cfg)).max_compaction_passes == 2   # env wins


def test_load_profile_context_reads_files(tmp_path):
    from resume_tailor.config import load_profile_context
    d = tmp_path / "profile"
    d.mkdir()
    (d / "02_second.md").write_text("Second achievement.")
    (d / "01_first.md").write_text("First achievement.")
    (d / "ignore.pdf").write_text("binary-ish, should be skipped")
    out = load_profile_context(str(d), 10000)
    # sorted order: 01 before 02
    assert out.index("First achievement") < out.index("Second achievement")
    assert "01_first.md" in out and "02_second.md" in out
    assert "ignore.pdf" not in out


def test_load_profile_context_respects_budget(tmp_path):
    from resume_tailor.config import load_profile_context
    d = tmp_path / "profile"
    d.mkdir()
    (d / "big.md").write_text("x" * 5000)
    out = load_profile_context(str(d), 500)
    assert len(out) <= 600  # header + truncated body + marker


def test_load_profile_context_empty_dir(tmp_path):
    from resume_tailor.config import load_profile_context
    assert load_profile_context("", 1000) == ""
    assert load_profile_context(str(tmp_path / "nope"), 1000) == ""
