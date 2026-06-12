#!/usr/bin/env bash
# resume-tailor uninstaller. Removes the launcher symlink and venv.
# Preserves your config, .env.local, and generated resumes by default.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
say() { printf "\033[1;34m==>\033[0m %s\n" "$*"; }

rm -f "$HOME/.local/bin/resume-tailor" && say "Removed launcher symlink"
rm -rf "$ROOT/.venv" && say "Removed virtualenv"

say "Kept: resume-tailor.toml, .env.local, examples/, and any generated output."
say "To remove those too: rm -f $ROOT/resume-tailor.toml $ROOT/.env.local"
say "The PATH line in your shell rc (if added) can be removed manually."
