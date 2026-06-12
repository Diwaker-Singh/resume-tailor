"""CLI: tailor a LaTeX resume to a job posting URL and emit a PDF.

Settings precedence (highest first): CLI flag > env var > config file > default.
See resume_tailor/config.py for the config-file format and search paths.
"""
from __future__ import annotations

import argparse
import difflib
import json
import shutil
import sys
from pathlib import Path

from . import __version__
from .compiler import CompileError, compile_tex, page_count, vertical_overflow
from .logging_setup import configure, get_logger
from .config import load as load_config
from .config import load_profile_context
from .config import slugify
from .llm.base import get_provider
from .scraper import scrape
from .tailor import tailor_tex


def _backup_previous(out_dir: Path, stem: str) -> list[str]:
    """Keep ONE prior version: move existing outputs to <name>.bak before a
    re-run overwrites them. Returns the names backed up. An older .bak is
    replaced (we keep exactly one previous version, not a history)."""
    backed: list[str] = []
    candidates = [f"{stem}.tex", f"{stem}.pdf", f"{stem}.diff", "job.json"]
    for name in candidates:
        src = out_dir / name
        if src.exists():
            bak = out_dir / (name + ".bak")
            if bak.exists():
                bak.unlink()
            src.rename(bak)
            backed.append(bak.name)
    return backed


def _write_diff(source_tex: str, tailored_tex: str, source_name: str,
                tailored_name: str, dest: Path) -> None:
    """Write a unified diff (source -> tailored) so you can see what changed at a
    glance. Opens with red/green highlighting in any editor or `git diff` viewer."""
    diff = difflib.unified_diff(
        source_tex.splitlines(keepends=True),
        tailored_tex.splitlines(keepends=True),
        fromfile=source_name,
        tofile=tailored_name,
        lineterm="\n",
    )
    dest.write_text("".join(diff), encoding="utf-8")


def _diff_hint(left: Path, right: Path) -> str | None:
    """Return a ready-to-paste `code --diff` command for side-by-side view, if a
    VS Code/Cursor-style CLI is available on PATH; else None."""
    for cli in ("code", "cursor", "codium"):
        if shutil.which(cli):
            return f"{cli} --diff '{left}' '{right}'"
    return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="resume-tailor",
        description="Tailor a LaTeX resume to a job posting and produce a PDF.",
    )
    p.add_argument("url", help="Job posting URL")
    p.add_argument("-r", "--resume", default=None,
                   help="Source .tex resume (overrides config/env)")
    p.add_argument("-o", "--out-dir", default=None,
                   help="Output parent directory (overrides config/env)")
    p.add_argument("--config", default=None,
                   help="Path to a config TOML (default: auto-discover)")
    p.add_argument("--provider", default=None,
                   help="LLM provider: gemini | openai | echo")
    p.add_argument("--model", default=None, help="Override model id")
    p.add_argument("--no-subdir", action="store_true",
                   help="Disable per-job output subfolder even if config enables it")
    p.add_argument("--no-pdf", action="store_true",
                   help="Only write the tailored .tex, skip PDF compile")
    p.add_argument("--no-backup", action="store_true",
                   help="Don't keep a .bak of a previous run's output before "
                        "overwriting (by default one prior version is preserved)")
    p.add_argument("--allow-overflow", action="store_true",
                   help="Accept a tailored resume that exceeds one page "
                        "(by default a multi-page result is an error)")
    p.add_argument("-v", "--verbose", action="count", default=0,
                   help="Increase log verbosity (-v info, -vv debug)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = p.parse_args(argv)

    configure(args.verbose)
    log = get_logger("cli")

    cfg = load_config(args.config)
    if cfg.source_path:
        print(f"      config: {cfg.source_path}", file=sys.stderr)
        log.debug("loaded config from %s", cfg.source_path)

    # precedence: CLI flag > (env-applied) config
    resume = args.resume or cfg.resume
    out_parent = args.out_dir or cfg.output_dir
    provider_name = args.provider or cfg.provider
    model = args.model or (cfg.model or None)
 
    if not resume:
        p.error("no resume given (use --resume, set RESUME_TAILOR_DEFAULT_RESUME, "
                "or set paths.resume in the config file)")
    resume_path = Path(resume).expanduser().resolve()
    if not resume_path.exists():
        p.error(f"resume not found: {resume_path}")
 
    # Initialize provider early so it can be used for naming
    provider = get_provider(provider_name, **({"model": model} if model else {}))
 
    print(f"[1/4] Scraping job: {args.url}", file=sys.stderr)
    job = scrape(args.url)
    print(f"      title={job.title!r} company={job.company!r} "
          f"desc={len(job.description)} chars source={job.source}", file=sys.stderr)
    if job.source == "text-fallback":
        print("      note: no JSON-LD found — used text fallback; "
              "check job.json to verify the scrape captured the full posting.",
              file=sys.stderr)
 
    # resolve output directory (optionally a per-job subfolder)
    out_dir = Path(out_parent).expanduser().resolve()
    use_subdir = cfg.per_job_subdir and not args.no_subdir
    if use_subdir:
        from datetime import date
        
        # Use LLM to get clean naming for the subfolder
        print(f"      extracting clean company/job name via LLM...", file=sys.stderr)
        from .tailor import _parse_naming
        company_clean, job_id_clean = _parse_naming(job, provider)
        
        sub = cfg.subdir_template.format(
            company=slugify(company_clean, "company"),
            title=slugify(job_id_clean, "role"),
            date=date.today().isoformat(),
            stem=resume_path.stem,
        )
        out_dir = out_dir / sub
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"      output: {out_dir}", file=sys.stderr)
 
    # Re-run safety: if a previous tailoring exists in this folder, keep exactly
    # one prior copy as <name>.bak before we overwrite it (skippable).
    stem = resume_path.stem + "_tailored"
    if not args.no_backup:
        backed = _backup_previous(out_dir, stem)
        if backed:
            print(f"      backed up previous run -> {', '.join(backed)}",
                  file=sys.stderr)
 
    # Persist the scraped job details for reference/audit.
    job_json = out_dir / "job.json"
    job_json.write_text(
        json.dumps(job.as_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"      saved scrape -> {job_json}", file=sys.stderr)
 
    print(f"[2/4] Tailoring via provider={provider_name}"
          f"{' model=' + model if model else ''}", file=sys.stderr)
    # provider already initialized above
    tex = resume_path.read_text(encoding="utf-8")


    # Optional profile context corpus (extra achievements not on the resume).
    profile_context = load_profile_context(
        cfg.profile_context_dir, cfg.profile_max_chars
    )
    if profile_context:
        print(f"      profile context: {len(profile_context)} chars from "
              f"{cfg.profile_context_dir}", file=sys.stderr)

    stem = resume_path.stem + "_tailored"
    out_tex = out_dir / (stem + ".tex")
    log_path = out_dir / (stem + ".log")

    # One-page compaction loop: tailor -> compile -> measure. If the result
    # overflows one page, re-tailor asking for progressively shorter prose,
    # up to max_passes times. With --no-pdf we can't measure, so single pass.
    max_passes = 1 if args.no_pdf else max(1, cfg.max_compaction_passes)
    pdf = None
    pages = overflow_pt = None
    for attempt in range(max_passes):
        shorten = attempt  # 0 first, then escalate
        try:
            new_tex, report = tailor_tex(tex, job, provider, shorten=shorten,
                                         profile_context=profile_context)
        except Exception as exc:  # LLM/provider failure (quota, network, etc.)
            log.debug("provider failure", exc_info=True)
            print(f"ERROR: LLM call failed — {exc}", file=sys.stderr)
            print("       No tailored resume produced; PDF skipped. "
                  "Re-run later, switch model (GEMINI_MODEL), or use --provider ollama. "
                  "Add -vv for the full error.", file=sys.stderr)
            return 4
        if attempt == 0:
            print(f"      regions={report['regions_found']} "
                  f"kind={report['region_kind']} applied={report['edits_applied']}",
                  file=sys.stderr)
        for note in report["notes"]:
            print(f"      note: {note}", file=sys.stderr)

        # If the FIRST pass produced no usable edits, the "tailored" resume is
        # identical to the source — don't write a misleading PDF. Surface why
        # and stop. (Later passes legitimately apply fewer edits; only the
        # initial pass producing nothing signals an LLM/quota failure.)
        if attempt == 0 and report["edits_applied"] == 0:
            print("WARNING: the LLM produced no applied edits — the resume is "
                  "unchanged from the source.", file=sys.stderr)
            if any("non-JSON" in n or "rate" in n.lower() or "quota" in n.lower()
                   for n in report["notes"]):
                print("       Likely an LLM/quota issue (see notes above). ",
                      file=sys.stderr, end="")
            print("Skipping PDF. Re-run later or check --provider/-vv.",
                  file=sys.stderr)
            # still write the (unchanged) .tex + an empty diff for transparency
            out_tex.write_text(new_tex, encoding="utf-8")
            _write_diff(tex, new_tex, resume_path.name, out_tex.name,
                        out_dir / (stem + ".diff"))
            return 5

        out_tex.write_text(new_tex, encoding="utf-8")
        if args.no_pdf:
            diff_path = out_dir / (stem + ".diff")
            _write_diff(tex, new_tex, resume_path.name, out_tex.name, diff_path)
            print(f"[3/4] Wrote {out_tex}", file=sys.stderr)
            print(f"      diff: {diff_path}", file=sys.stderr)
            hint = _diff_hint(resume_path, out_tex)
            if hint:
                print(f"      side-by-side: {hint}", file=sys.stderr)
            print(str(out_tex))
            return 0

        label = "Compiling PDF" if attempt == 0 else f"Recompiling (shorten pass {attempt})"
        print(f"[3/4] {label}...", file=sys.stderr)
        try:
            pdf = compile_tex(out_tex, out_dir)
        except CompileError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

        pages = page_count(log_path, pdf)
        overflow_pt = vertical_overflow(log_path)
        fits = (pages is None or pages <= 1) and (overflow_pt or 0) <= 1.0
        if fits:
            if attempt:
                print(f"      fit on one page after {attempt} compaction pass(es)",
                      file=sys.stderr)
            break
        if attempt < max_passes - 1:
            why = (f"{pages} pages" if (pages and pages > 1)
                   else f"overflow {overflow_pt:.0f}pt")
            print(f"      too long ({why}) — re-tailoring shorter "
                  f"[pass {attempt + 1}/{max_passes - 1}]", file=sys.stderr)

    print(f"[4/4] Wrote {out_tex}", file=sys.stderr)

    # Write a unified diff (source -> tailored) for quick "what changed" review.
    diff_path = out_dir / (stem + ".diff")
    _write_diff(tex, new_tex, resume_path.name, out_tex.name, diff_path)
    print(f"      diff: {diff_path}", file=sys.stderr)
    hint = _diff_hint(resume_path, out_tex)
    if hint:
        print(f"      side-by-side: {hint}", file=sys.stderr)

    # Final verdict on the one-page constraint.
    too_long = (pages is not None and pages > 1) or (overflow_pt or 0) > 1.0
    if too_long and not args.allow_overflow:
        reason = (f"{pages} pages" if (pages and pages > 1)
                  else f"content overflows the page by {overflow_pt:.0f}pt")
        print(
            f"ERROR: tailored resume does not fit on one page ({reason}) after "
            f"{max_passes} attempt(s).\n"
            f"       The PDF was written to {pdf} for inspection.\n"
            "       Fixes: tighten the source bullets, raise "
            "output.max_compaction_passes,\n"
            "       or pass --allow-overflow to accept it anyway.",
            file=sys.stderr,
        )
        return 3
    if pages is not None:
        print(f"      pages={pages}"
              + (f" (overflow {overflow_pt:.0f}pt)" if (overflow_pt or 0) > 1.0 else ""),
              file=sys.stderr)
    print(str(pdf))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
