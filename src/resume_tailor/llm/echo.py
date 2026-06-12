"""Offline test provider: echoes back valid JSON with unchanged regions.

Lets the full pipeline (scrape -> edit -> compile) run with zero network/keys.
"""
from __future__ import annotations

import json
import re

from .base import LLMProvider


class EchoProvider(LLMProvider):
    name = "echo"

    def complete(self, system: str, user: str) -> str:
        # Pull region ids out of the prompt and return them unchanged.
        rids = re.findall(r'"(marker:[^"]+|item:\d+)"', user)
        bodies = {}
        # crude: parse the JSON block we embedded in the user prompt
        m = re.search(r"REGIONS_JSON:\s*(\{.*\})\s*$", user, re.DOTALL)
        if m:
            try:
                bodies = json.loads(m.group(1))
            except json.JSONDecodeError:
                bodies = {}

        def _text(v):
            # payload values are now {"text": ..., "max_lines": ...}; tolerate
            # both the dict shape and a bare string for backwards-compat.
            return v.get("text", "") if isinstance(v, dict) else v

        if bodies:
            edits = {rid: _text(v) for rid, v in bodies.items()}
        else:
            edits = {rid: "" for rid in rids}
        return json.dumps({"edits": edits})
