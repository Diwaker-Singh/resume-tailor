"""Central logging for resume-tailor.

Library modules use `logging.getLogger("resume_tailor.<module>")`; the CLI calls
configure() once to attach a stderr handler. Verbosity via -v/-vv or
$RESUME_TAILOR_LOG_LEVEL (DEBUG/INFO/WARNING/ERROR).

User-facing step progress in the CLI stays as plain stderr prints; logging is
for diagnostics (LLM calls, retries, fallbacks, timings) that you can dial up
when something goes wrong.
"""
from __future__ import annotations

import logging
import os

_CONFIGURED = False


def configure(verbosity: int = 0) -> None:
    """Attach a single stderr handler at the chosen level. Idempotent."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    env = os.environ.get("RESUME_TAILOR_LOG_LEVEL", "").upper()
    if env in ("DEBUG", "INFO", "WARNING", "ERROR"):
        level = getattr(logging, env)
    else:
        level = {0: logging.WARNING, 1: logging.INFO}.get(verbosity, logging.DEBUG)
    handler = logging.StreamHandler()  # stderr
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("resume_tailor")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"resume_tailor.{name}")
