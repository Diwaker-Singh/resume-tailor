r"""LaTeX-aware editing that CANNOT break document structure.

Two tailorable region types:
  1. Marker regions:  % <TAILOR:name> ... % </TAILOR>
     -> free prose between the markers is editable.
  2. Auto-detected \item bullets (fallback when no markers present).

Editing model: the LLM returns *replacement text* keyed by a stable region id.
We splice replacements via exact-span substitution on the original byte string,
so everything outside a region is preserved byte-for-byte.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ---- region extraction ---------------------------------------------------

_MARKER_RE = re.compile(
    r"%\s*<TAILOR:(?P<name>[A-Za-z0-9_-]+)>\s*\n(?P<body>.*?)\n[ \t]*%\s*</TAILOR>",
    re.DOTALL,
)

# \item ... up to the next \item or \end{...} (newline-agnostic, non-greedy).
_ITEM_RE = re.compile(
    r"\\item\b[ \t]*(?P<body>.*?)(?=\\item\b|\\end\b|\Z)",
    re.DOTALL,
)


@dataclass
class Region:
    rid: str           # stable id, e.g. "marker:summary" or "item:7"
    start: int         # offset of editable body in source
    end: int
    text: str          # current body text
    kind: str          # "marker" | "item"


def extract_regions(tex: str) -> list[Region]:
    regions: list[Region] = []

    for m in _MARKER_RE.finditer(tex):
        regions.append(
            Region(
                rid=f"marker:{m.group('name')}",
                start=m.start("body"),
                end=m.end("body"),
                text=m.group("body"),
                kind="marker",
            )
        )

    if not regions:  # fallback: bullets
        for i, m in enumerate(_ITEM_RE.finditer(tex)):
            body = m.group("body")
            if body.strip():
                regions.append(
                    Region(
                        rid=f"item:{i}",
                        start=m.start("body"),
                        end=m.end("body"),
                        text=body,
                        kind="item",
                    )
                )
    return regions


# ---- safety validation ----------------------------------------------------

def _balanced_braces(s: str) -> bool:
    depth = 0
    esc = False
    for ch in s:
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


_FORBIDDEN = re.compile(r"\\(begin|end|documentclass|usepackage|section|subsection)\b")
_ITEM_TOKEN = re.compile(r"\\item\b")
# bold "label:" group headers, e.g. \textbf{Languages:} — used to keep a grouped
# block (like the skills section) readable rather than collapsed into a run-on.
_BOLD_LABEL = re.compile(r"\\textbf\s*\{[^}]*:\s*\}")

# --- rendered-line estimation ---------------------------------------------
# We size a region by the number of *rendered* lines it occupies, not raw
# source chars, because that maps to vertical space on the page. Approximate
# rendered width per text line at 11pt over the resume's text column. The exact
# value matters little: we compare the NEW text against the ORIGINAL with the
# SAME estimator, so any constant error cancels out (self-calibrating).
_CHARS_PER_LINE = 95

# strip the LaTeX markup that doesn't render as visible width
_TEX_CMD_ARG = re.compile(r"\\[a-zA-Z]+\*?\{")        # \textbf{ , \hl{ , \href{...}{
_TEX_CMD_BARE = re.compile(r"\\[a-zA-Z]+\*?")          # \item, \quad, \\
_TEX_MATH = re.compile(r"\$[^$]*\$")                   # $\sim$, $>$ -> ~1-2 chars
_TEX_BRACES = re.compile(r"[{}]")
_WS = re.compile(r"\s+")


def _visible_text(s: str) -> str:
    """Approximate the text a region renders to, stripping LaTeX control words,
    math, and braces so a length estimate reflects on-page width."""
    s = _TEX_MATH.sub("~", s)            # math atoms render very short
    s = _TEX_CMD_ARG.sub("", s)          # drop "\cmd{" openers, keep the arg
    s = _TEX_CMD_BARE.sub("", s)         # drop bare commands
    s = _TEX_BRACES.sub("", s)
    s = _WS.sub(" ", s).strip()
    return s


def rendered_lines(region_text: str) -> int:
    """Estimate the number of rendered lines a region occupies.

    Each bullet (\\item) is its own wrapped paragraph; a markerless paragraph is
    a single wrapped block. Wrapping is estimated as ceil(visible_chars / CPL),
    minimum one line per bullet/paragraph."""
    items = _ITEM_TOKEN.split(region_text)
    # split() yields a leading chunk before the first \item; ignore if blank
    units = [u for u in items if u.strip()] or [region_text]
    total = 0
    for u in units:
        vis = _visible_text(u)
        total += max(1, -(-len(vis) // _CHARS_PER_LINE))  # ceil division
    return total


def is_safe_replacement(original: str, new: str) -> tuple[bool, str]:
    """Reject replacements that could break compilation/structure or grow the
    region's *rendered height* beyond the original.

    Beyond brace balance and banned structural commands, this preserves the
    *structural shape* of the region (paragraph stays paragraph, bulleted stays
    bulleted) and the *line budget*: the rewritten region may not render to more
    lines than the original. Sizing by rendered lines (not source chars) ties
    the limit to vertical page space and keeps the resume one page.
    """
    if not _balanced_braces(new):
        return False, "unbalanced braces"
    if _FORBIDDEN.search(new) and not _FORBIDDEN.search(original):
        return False, "introduces structural command"
    orig_items = len(_ITEM_TOKEN.findall(original))
    new_items = len(_ITEM_TOKEN.findall(new))
    if orig_items == 0 and new_items > 0:
        return False, "introduces \\item into a paragraph region"
    if orig_items > 0 and new_items == 0:
        return False, "removes all bullets from a list region"
    if new_items != orig_items:
        return False, (
            f"bullet count changed ({orig_items} -> {new_items}); keep the same "
            "number of \\item bullets"
        )
    # Readability: a grouped block (e.g. the skills section with several
    # \textbf{Label:} groups) must stay grouped — never collapse into a single
    # run-on. If the original had >=2 bold label groups, require the rewrite to
    # keep at least 3 (or the original count if it had fewer).
    orig_groups = len(_BOLD_LABEL.findall(original))
    if orig_groups >= 2:
        new_groups = len(_BOLD_LABEL.findall(new))
        min_groups = min(3, orig_groups)
        if new_groups < min_groups:
            return False, (
                f"too few labeled groups ({new_groups} < {min_groups}); keep the "
                "skills grouped into short bold-labeled categories, not a run-on"
            )
    # Line budget: the rewrite must not render to more lines than the original.
    orig_lines = rendered_lines(original)
    new_lines = rendered_lines(new)
    if new_lines > orig_lines:
        return False, (
            f"too many lines ({new_lines} > {orig_lines}); keep the region within "
            "its original line count to preserve the one-page layout"
        )
    return True, ""


# ---- splice ---------------------------------------------------------------

def apply_edits(tex: str, regions: list[Region], edits: dict[str, str]) -> tuple[str, list[str]]:
    """Apply {rid: new_text}. Returns (new_tex, notes). Skips unsafe edits."""
    notes: list[str] = []
    # apply right-to-left so offsets stay valid
    ordered = sorted(
        [r for r in regions if r.rid in edits], key=lambda r: r.start, reverse=True
    )
    out = tex
    for r in ordered:
        new = edits[r.rid]
        ok, why = is_safe_replacement(r.text, new)
        if not ok:
            notes.append(f"skipped {r.rid}: {why}")
            continue
        out = out[: r.start] + new + out[r.end :]
    return out, notes
