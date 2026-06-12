#!/usr/bin/env bash
# resume-tailor installer (macOS / Linux).
# Idempotent AND self-healing: safe to re-run any number of times. Detects and
# repairs a broken virtualenv, missing deps, lost +x bits, and PATH/symlink drift.
#
# Flags:
#   --repair / --force   Force-recreate the virtualenv from scratch.
set -uo pipefail   # NOTE: not -e; we handle errors explicitly so one failing
                   # step can't abort the rest of the heal.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
say()  { printf "\033[1;34m==>\033[0m %s\n" "$*"; }
ok()   { printf "\033[1;32m[ok]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[!]\033[0m %s\n" "$*"; }
err()  { printf "\033[1;31m[x]\033[0m %s\n" "$*"; }

FORCE=0
for a in "$@"; do
  case "$a" in
    --repair|--force) FORCE=1 ;;
    -h|--help) echo "usage: ./install.sh [--repair]"; exit 0 ;;
  esac
done

FAILED=0

# 1) Python virtualenv (self-healing) -------------------------------------
# Healthy means: the interpreter runs AND it's a real venv (has its own prefix).
# We check sys.prefix differs from base_prefix so a dangling/foreign python is
# treated as broken and rebuilt.
venv_ok() {
  [ -x ".venv/bin/python" ] || return 1
  .venv/bin/python -c 'import sys; sys.exit(0 if sys.prefix != sys.base_prefix else 1)' \
    >/dev/null 2>&1
}

if [ "$FORCE" = "1" ] && [ -d .venv ]; then
  say "Repair requested — removing existing .venv"
  rm -rf .venv
fi

if venv_ok; then
  ok "Virtualenv healthy"
else
  if [ -e .venv ]; then
    warn "Virtualenv missing or broken — recreating"
    rm -rf .venv
  else
    say "Creating virtualenv (.venv)"
  fi
  if ! python3 -m venv .venv; then
    err "Failed to create virtualenv with $(command -v python3)"; exit 1
  fi
fi

# 2) Dependencies — editable install with dev extras (pip is idempotent) --
say "Installing the package (editable) + dependencies"
.venv/bin/python -m pip install --quiet --upgrade pip || warn "pip self-upgrade failed (continuing)"
if .venv/bin/python -m pip install --quiet -e ".[dev]"; then
  ok "Package installed (editable, with dev extras)"
else
  err "pip install failed — check network/proxy"; FAILED=1
fi
# verify the package + its deps actually import
if .venv/bin/python -c 'import resume_tailor, httpx, bs4, lxml' >/dev/null 2>&1; then
  ok "Imports verified (resume_tailor, httpx, bs4, lxml)"
else
  err "Imports still failing after install"; FAILED=1
fi

# 3) LaTeX engine check (read-only) ---------------------------------------
PATH_WITH_TEX="$PATH"; [ -d /Library/TeX/texbin ] && PATH_WITH_TEX="/Library/TeX/texbin:$PATH"
if PATH="$PATH_WITH_TEX" command -v pdflatex >/dev/null 2>&1 \
   || PATH="$PATH_WITH_TEX" command -v latexmk >/dev/null 2>&1; then
  ok "LaTeX engine found ($(PATH="$PATH_WITH_TEX" command -v pdflatex latexmk 2>/dev/null | head -1))"
else
  warn "No LaTeX engine found. PDF output needs one. On macOS:"
  warn "    brew install --cask basictex"
  warn "    sudo /Library/TeX/texbin/tlmgr install fontawesome5 enumitem titlesec parskip"
  warn "  (Tectonic works too but crashes on fontawesome5 on recent macOS.)"
fi

# 4) Config bootstrap ------------------------------------------------------
if [ ! -f "resume-tailor.toml" ]; then
  if [ -f "resume-tailor.example.toml" ]; then
    say "Creating resume-tailor.toml from template"
    sed "s#~/workplace/resume-tailor#$ROOT#g" resume-tailor.example.toml > resume-tailor.toml
    warn "Edit resume-tailor.toml to set your resume path + output_dir."
  else
    warn "resume-tailor.example.toml missing — cannot bootstrap config"
  fi
else
  ok "Config resume-tailor.toml present (left untouched)"
fi

# 5) API key ---------------------------------------------------------------
if [ ! -f ".env.local" ]; then
  if [ -t 0 ]; then
    say "Gemini API key setup (free key: https://aistudio.google.com/app/apikey)"
    read -r -p "    Paste GEMINI_API_KEY (or leave blank to skip): " KEY || true
    if [ -n "${KEY:-}" ]; then
      printf 'GEMINI_API_KEY=%s\n' "$KEY" > .env.local
      chmod 600 .env.local
      ok "Saved .env.local (gitignored, perms 600)"
    else
      warn "Skipped. Create .env.local later with: GEMINI_API_KEY=..."
    fi
  else
    warn "No TTY; skipping key prompt. Create .env.local with GEMINI_API_KEY=..."
  fi
else
  ok ".env.local present (left untouched)"
  # heal perms if they drifted
  [ "$(stat -f%Lp .env.local 2>/dev/null || stat -c%a .env.local 2>/dev/null)" = "600" ] \
    || { chmod 600 .env.local; say "Reset .env.local perms to 600"; }
fi

# 6) Launcher: ensure executable + symlinked on PATH ----------------------
[ -x "$ROOT/resume-tailor" ] || { chmod +x "$ROOT/resume-tailor" && say "Restored +x on launcher"; }
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"
# (re)create the symlink only if missing or pointing elsewhere
LINK="$BIN_DIR/resume-tailor"
CUR_TARGET="$(readlink "$LINK" 2>/dev/null || true)"
if [ "$CUR_TARGET" = "$ROOT/resume-tailor" ]; then
  ok "Launcher symlink already correct"
elif [ -n "$CUR_TARGET" ]; then
  # an existing resume-tailor command points at a DIFFERENT install
  warn "$LINK already points to: $CUR_TARGET"
  warn "Repointing it to this install: $ROOT/resume-tailor"
  ln -sf "$ROOT/resume-tailor" "$LINK"
  ok "Relinked launcher -> $LINK"
elif [ -e "$LINK" ]; then
  warn "$LINK exists and is not a symlink — leaving it untouched."
  warn "Run via: $ROOT/resume-tailor"
else
  ln -sf "$ROOT/resume-tailor" "$LINK"
  ok "Linked launcher -> $LINK"
fi

case ":$PATH:" in
  *":$BIN_DIR:"*) ok "$BIN_DIR already on PATH" ;;
  *)
    case "${SHELL:-}" in
      *zsh)  RCS=("$HOME/.zshrc") ;;
      *bash) RCS=("$HOME/.bashrc" "$HOME/.bash_profile") ;;
      *)     RCS=("$HOME/.zshrc" "$HOME/.bashrc") ;;
    esac
    for SHELL_RC in "${RCS[@]}"; do
      [ -e "$SHELL_RC" ] || [ "$SHELL_RC" = "${RCS[0]}" ] || continue
      if ! grep -q 'resume-tailor: add ~/.local/bin' "$SHELL_RC" 2>/dev/null; then
        {
          echo ''
          echo '# resume-tailor: add ~/.local/bin to PATH'
          echo 'export PATH="$HOME/.local/bin:$PATH"'
        } >> "$SHELL_RC"
        say "Added ~/.local/bin to PATH in $SHELL_RC"
      fi
    done
    warn "Open a new terminal (or 'source ${RCS[0]}') to pick up the PATH change."
    ;;
esac

# 7) Smoke test ------------------------------------------------------------
say "Running tests"
if ! .venv/bin/python -c 'import pytest' >/dev/null 2>&1; then
  warn "pytest not installed — skipping smoke test"
elif PATH="$PATH_WITH_TEX" .venv/bin/python -m pytest -q >/dev/null 2>&1; then
  ok "All tests pass."
else
  warn "Some tests failed — run: .venv/bin/python -m pytest"
  FAILED=1
fi

echo
if [ "$FAILED" = "0" ]; then
  ok "Install/heal complete. Usage:  resume-tailor \"<job-url>\""
else
  err "Install completed with warnings above. Re-run: ./install.sh --repair"
  exit 1
fi
