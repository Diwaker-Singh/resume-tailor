"""Compile LaTeX -> PDF. Prefers pdflatex/latexmk; tectonic as a fallback."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path


class CompileError(RuntimeError):
    pass


# Common install locations not always on a non-login shell's PATH.
_EXTRA_BIN_DIRS = [
    "/Library/TeX/texbin",          # macOS MacTeX / BasicTeX
    "/usr/local/texlive/2026/bin/universal-darwin",
    "/opt/homebrew/bin",            # macOS Homebrew (tectonic)
    "/usr/local/bin",
]


def _which(name: str) -> str | None:
    """Like shutil.which, but also searches known TeX/brew bin dirs so the tool
    works under bare `pytest`/cron where PATH may be minimal."""
    found = shutil.which(name)
    if found:
        return found
    for d in _EXTRA_BIN_DIRS:
        cand = os.path.join(d, name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def has_engine() -> bool:
    """True if any supported LaTeX engine is reachable."""
    return any(_which(e) for e in ("latexmk", "pdflatex", "tectonic"))


def _find_engine() -> tuple[str, list[str]]:
    # Prefer pdflatex/latexmk: they handle fontawesome5 reliably. Tectonic is a
    # convenient single-binary fallback, but tectonic >=0.16 crashes on
    # fontawesome5 (OpenType path) on recent macOS, so it is tried last.
    latexmk = _which("latexmk")
    if latexmk:
        return "latexmk", [latexmk, "-pdf", "-interaction=nonstopmode"]
    pdflatex = _which("pdflatex")
    if pdflatex:
        return "pdflatex", [pdflatex, "-interaction=nonstopmode"]
    tectonic = _which("tectonic")
    if tectonic:
        # Note: tectonic >=0.15 treats --synctex as a valueless flag, so we
        # simply omit it (default is synctex off) for cross-version compatibility.
        return "tectonic", [tectonic, "--keep-logs"]
    raise CompileError(
        "No LaTeX engine found. On macOS install BasicTeX (recommended): "
        "`brew install --cask basictex` then `sudo tlmgr install fontawesome5 "
        "enumitem titlesec parskip`. Tectonic (`brew install tectonic`) also "
        "works but currently crashes on fontawesome5 on recent macOS."
    )


def compile_tex(tex_path: str | Path, out_dir: str | Path | None = None) -> Path:
    tex_path = Path(tex_path).resolve()
    out_dir = Path(out_dir).resolve() if out_dir else tex_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    engine, base_cmd = _find_engine()

    if engine == "tectonic":
        cmd = base_cmd + ["--outdir", str(out_dir), str(tex_path)]
    else:
        cmd = base_cmd + [f"-output-directory={out_dir}", str(tex_path)]

    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=tex_path.parent)
    pdf = out_dir / (tex_path.stem + ".pdf")

    # 1) No PDF at all => hard failure.
    if not pdf.exists() or pdf.stat().st_size == 0:
        tail = (proc.stdout + "\n" + proc.stderr)[-2000:]
        raise CompileError(f"{engine} failed (no PDF produced):\n{tail}")

    # 2) A PDF can still be emitted while the source has real LaTeX errors
    #    (e.g. a dangling \textcolor argument). pdflatex logs these as lines
    #    starting with "! ". Treat ANY such error as a failure, even though a
    #    (degraded) PDF exists — a broken render must never pass silently.
    errors = _latex_errors(out_dir / (tex_path.stem + ".log"), proc.stdout)
    if errors:
        joined = "\n".join(errors[:10])
        raise CompileError(
            f"{engine} produced a PDF but reported LaTeX errors:\n{joined}"
        )
    return pdf


def _latex_errors(log_path: Path, stdout: str) -> list[str]:
    """Return LaTeX error lines (those starting with '! ') from the log/stdout."""
    text = ""
    if log_path.exists():
        text = log_path.read_text(errors="replace")
    if not text:
        text = stdout
    return [ln for ln in text.splitlines() if ln.startswith("! ")]


def vertical_overflow(log_path: str | Path | None) -> float:
    """Return the largest 'Overfull \\vbox (N.Npt too high)' value in points
    from the LaTeX log, or 0.0 if none. A nonzero value means content spilled
    past the bottom of the page even if the PDF is still one page."""
    if not log_path:
        return 0.0
    lp = Path(log_path)
    if not lp.exists():
        return 0.0
    worst = 0.0
    for m in re.finditer(r"Overfull \\vbox \(([0-9.]+)pt too high\)", lp.read_text(errors="replace")):
        worst = max(worst, float(m.group(1)))
    return worst


def page_count(log_path: str | Path | None = None, pdf_path: str | Path | None = None) -> int | None:
    """Best-effort page count.

    Prefers the LaTeX log ("Output written on X.pdf (N pages...)"); falls back
    to counting /Type /Page objects in the PDF. Returns None if unknown.
    """
    # 1) parse the LaTeX log
    if log_path:
        lp = Path(log_path)
        if lp.exists():
            m = re.search(r"Output written on .*?\((\d+) pages?", lp.read_text(errors="replace"))
            if m:
                return int(m.group(1))
    # 2) fall back to scanning the PDF bytes
    if pdf_path:
        pp = Path(pdf_path)
        if pp.exists():
            data = pp.read_bytes()
            n = len(re.findall(rb"/Type\s*/Page[^s]", data))
            if n:
                return n
    return None
