"""Layered configuration for resume-tailor.

Precedence (highest first):
  1. CLI flags (handled in cli.py)
  2. Environment variables
  3. Config file (TOML)
  4. Built-in defaults

Config file is discovered in this order:
  1. path passed explicitly (``--config`` / ``load(path=...)``)
  2. ``$RESUME_TAILOR_CONFIG``
  3. ``./resume-tailor.toml`` (current directory)
  4. ``~/.config/resume-tailor/config.toml``

Example config.toml:

    [paths]
    resume      = "~/workplace/resume-tailor/examples/sample_resume.tex"
    output_dir  = "~/Documents/Tailored_Resumes"

    [output]
    per_job_subdir  = true
    subdir_template = "{company}-{title}"   # {company} {title} {date} {stem}

    [llm]
    provider = "gemini"
    model    = "gemini-flash-lite-latest"
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# tomllib is stdlib on Python 3.11+; fall back to the `tomli` backport on 3.10.
try:
    import tomllib  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - 3.10 only
    import tomli as tomllib  # type: ignore

# ---- built-in defaults ----------------------------------------------------

_DEFAULTS = {
    "paths": {
        "resume": "",
        "output_dir": "./out",
    },
    "output": {
        "per_job_subdir": False,
        "subdir_template": "{company}-{title}",
        # How many times to re-tailor (asking for shorter prose) when the PDF
        # overflows one page. 1 = single pass, no compaction retries.
        "max_compaction_passes": 4,
    },
    "llm": {
        "provider": "gemini",
        "model": "",  # empty => provider's own default
    },
    "profile": {
        # Folder of .md/.txt files with extra achievements/content that don't
        # fit on the one-page resume. The LLM may DRAW FROM these (facts only,
        # never invented) when a job calls for experience not already on the
        # resume. Empty => no profile context fed.
        "context_dir": "",
        # Hard cap on how many chars of profile context to feed the LLM.
        "max_chars": 12000,
    },
}

_ENV_CONFIG = "RESUME_TAILOR_CONFIG"
_SEARCH = [
    Path("./resume-tailor.toml"),
    Path("~/.config/resume-tailor/config.toml").expanduser(),
]


@dataclass
class Config:
    resume: str = ""
    output_dir: str = "./out"
    per_job_subdir: bool = False
    subdir_template: str = "{company}-{title}"
    max_compaction_passes: int = 4
    provider: str = "gemini"
    model: str = ""
    profile_context_dir: str = ""
    profile_max_chars: int = 12000
    source_path: Path | None = None  # which config file was loaded (if any)

    # env var overrides applied on top of file/defaults
    def _apply_env(self) -> "Config":
        self.resume = os.environ.get("RESUME_TAILOR_DEFAULT_RESUME", self.resume)
        self.output_dir = os.environ.get("RESUME_TAILOR_OUTPUT_DIR", self.output_dir)
        self.provider = os.environ.get("RESUME_TAILOR_PROVIDER", self.provider)
        self.model = os.environ.get("RESUME_TAILOR_MODEL", self.model)
        env_passes = os.environ.get("RESUME_TAILOR_MAX_COMPACTION_PASSES")
        if env_passes and env_passes.isdigit():
            self.max_compaction_passes = int(env_passes)
        return self


def _find_config(path: str | None) -> Path | None:
    if path:
        p = Path(path).expanduser()
        return p if p.exists() else None
    env = os.environ.get(_ENV_CONFIG)
    if env:
        p = Path(env).expanduser()
        if p.exists():
            return p
    for cand in _SEARCH:
        if cand.exists():
            return cand
    return None


def load(path: str | None = None) -> Config:
    """Load config from defaults <- file <- env (env wins over file)."""
    merged = {k: dict(v) for k, v in _DEFAULTS.items()}
    src = _find_config(path)
    if src:
        with src.open("rb") as fh:
            data = tomllib.load(fh)
        for section, values in data.items():
            if section in merged and isinstance(values, dict):
                merged[section].update(values)

    cfg = Config(
        resume=merged["paths"]["resume"],
        output_dir=merged["paths"]["output_dir"],
        per_job_subdir=bool(merged["output"]["per_job_subdir"]),
        subdir_template=merged["output"]["subdir_template"],
        max_compaction_passes=int(merged["output"]["max_compaction_passes"]),
        provider=merged["llm"]["provider"],
        model=merged["llm"]["model"],
        profile_context_dir=merged["profile"]["context_dir"],
        profile_max_chars=int(merged["profile"]["max_chars"]),
        source_path=src,
    )
    return cfg._apply_env()


def slugify(text: str, fallback: str = "job") -> str:
    """Filesystem-safe slug for company/title in subdir names."""
    import re

    s = re.sub(r"[^\w\s-]", "", (text or "").lower()).strip()
    s = re.sub(r"[\s_-]+", "-", s)
    return s[:60] or fallback


def load_profile_context(context_dir: str, max_chars: int) -> str:
    """Concatenate all .md/.txt files in context_dir into a single reference
    string (capped at max_chars). Returns "" if no dir/files. Files are read in
    sorted filename order so ordering is deterministic and user-controllable
    (prefix files with 01_, 02_ to prioritise)."""
    if not context_dir:
        return ""
    d = Path(context_dir).expanduser()
    if not d.is_dir():
        return ""
    chunks: list[str] = []
    used = 0
    for f in sorted(d.iterdir()):
        if f.suffix.lower() not in (".md", ".txt") or not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if not text:
            continue
        header = f"\n\n===== {f.name} =====\n"
        remaining = max_chars - used - len(header)
        if remaining <= 0:
            break
        if len(text) > remaining:
            text = text[:remaining] + "\n...[truncated]"
        chunks.append(header + text)
        used += len(header) + len(text)
    return "".join(chunks).strip()
