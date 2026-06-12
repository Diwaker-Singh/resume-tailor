"""LaTeX region extraction + safe splicing. This module is the layout-safety
guarantee: edits must never alter structure outside a tailorable region."""
from resume_tailor.latex import (
    apply_edits,
    extract_regions,
    is_safe_replacement,
)


# ---- region extraction ----------------------------------------------------


def test_marker_region_basic():
    tex = "intro\n% <TAILOR:summary>\nold text here\n% </TAILOR>\noutro"
    regions = extract_regions(tex)
    assert len(regions) == 1
    assert regions[0].rid == "marker:summary"
    assert regions[0].text == "old text here"
    assert regions[0].kind == "marker"

def test_multiple_marker_regions_preserve_order_and_names():
    tex = (
        "% <TAILOR:a>\nAAA\n% </TAILOR>\n"
        "middle\n"
        "% <TAILOR:b>\nBBB\n% </TAILOR>\n"
    )
    regions = extract_regions(tex)
    assert [r.rid for r in regions] == ["marker:a", "marker:b"]

def test_item_fallback_when_no_markers():
    tex = r"\begin{itemize}\item first bullet\item second bullet\end{itemize}"
    regions = extract_regions(tex)
    assert len(regions) == 2
    assert all(r.kind == "item" for r in regions)
    assert regions[0].text.strip() == "first bullet"

def test_markers_take_precedence_over_items():
    tex = (
        r"\begin{itemize}"
        "\n% <TAILOR:bullets>\n"
        r"\item one \item two"
        "\n% </TAILOR>\n"
        r"\end{itemize}"
    )
    regions = extract_regions(tex)
    # only the marker region, not individual items
    assert len(regions) == 1
    assert regions[0].rid == "marker:bullets"

def test_no_regions_returns_empty():
    assert extract_regions(r"\documentclass{article}\begin{document}hi\end{document}") == []


# ---- safety validation ----------------------------------------------------

def test_rejects_unbalanced_braces():
    ok, why = is_safe_replacement("old", "new {unbalanced")
    assert not ok and "brace" in why.lower()

def test_rejects_extra_closing_brace():
    ok, why = is_safe_replacement("old", "new} extra")
    assert not ok

def test_rejects_structural_commands():
    for cmd in (r"\section{X}", r"\begin{tabular}", r"\usepackage{x}",
                r"\documentclass{y}", r"\subsection{z}"):
        ok, why = is_safe_replacement("old text", f"text {cmd}")
        assert not ok, f"should reject {cmd}"

def test_allows_structural_command_if_present_in_original():
    # if the original already had it, a replacement keeping it is allowed
    ok, _ = is_safe_replacement(r"a \section{X} b", r"c \section{X} d")
    assert ok

def test_rejects_runaway_length():
    ok, why = is_safe_replacement("short", "x" * 5000)
    assert not ok and "line" in why.lower()

def test_allows_inline_formatting():
    for good in (r"new \textbf{bold} text", r"\hl{highlight} \textit{it}",
                 r"value $\sim$80\% precision", r"a \href{http://x}{link}"):
        ok, why = is_safe_replacement("old", good)
        assert ok, f"should allow {good!r}: {why}"

def test_escaped_brace_does_not_unbalance():
    ok, _ = is_safe_replacement("old", r"100\% and \{literal\} braces")
    assert ok


# ---- apply_edits ----------------------------------------------------------

def test_apply_preserves_surrounding_bytes():
    tex = "A\n% <TAILOR:s>\nold\n% </TAILOR>\nZ"
    regions = extract_regions(tex)
    new, notes = apply_edits(tex, regions, {"marker:s": "new prose"})
    assert "new prose" in new
    assert new.startswith("A\n% <TAILOR:s>")
    assert new.endswith("% </TAILOR>\nZ")
    assert notes == []

def test_apply_multiple_edits_offsets_stay_valid():
    tex = "% <TAILOR:a>\nAAA\n% </TAILOR>\n% <TAILOR:b>\nBBB\n% </TAILOR>"
    regions = extract_regions(tex)
    new, notes = apply_edits(tex, regions, {"marker:a": "X1", "marker:b": "Y2"})
    assert "X1" in new and "Y2" in new
    assert "AAA" not in new and "BBB" not in new
    assert notes == []

def test_apply_skips_unsafe_edit_and_notes_it():
    tex = "% <TAILOR:s>\nold\n% </TAILOR>"
    regions = extract_regions(tex)
    new, notes = apply_edits(tex, regions, {"marker:s": "broken {brace"})
    assert "old" in new           # unchanged
    assert "broken" not in new
    assert any("marker:s" in n for n in notes)

def test_apply_ignores_unknown_region_ids():
    tex = "% <TAILOR:s>\nold\n% </TAILOR>"
    regions = extract_regions(tex)
    new, notes = apply_edits(tex, regions, {"marker:does_not_exist": "x"})
    assert new == tex

def test_apply_partial_edit_only_touches_named_region():
    tex = "% <TAILOR:a>\nAAA\n% </TAILOR>\n% <TAILOR:b>\nBBB\n% </TAILOR>"
    regions = extract_regions(tex)
    new, _ = apply_edits(tex, regions, {"marker:a": "X"})
    assert "X" in new and "BBB" in new and "AAA" not in new


# ---- structure-shape preservation (paragraph vs bullets) ------------------

def test_rejects_item_injected_into_paragraph():
    ok, why = is_safe_replacement(
        "Plain prose describing a single role.",
        r"\item now it is a bullet",
    )
    assert not ok and "item" in why.lower()

def test_rejects_removing_all_bullets_from_list():
    ok, why = is_safe_replacement(
        r"\item one \item two",
        "flattened into prose with no bullets",
    )
    assert not ok and "bullet" in why.lower()

def test_allows_paragraph_rewrite_staying_paragraph():
    ok, why = is_safe_replacement(
        "Finance Automation team using Kotlin.",
        r"\hl{Finance Automation} team building event-driven microservices in Kotlin.",
    )
    assert ok, why

def test_allows_bullets_rewrite_keeping_items():
    ok, why = is_safe_replacement(
        r"\item old one \item old two",
        r"\item new one \item new two",
    )
    assert ok, why
    assert ok, why


# ---- structure-shape preservation (paragraph vs bullets) ------------------


# ---- rendered-line budget -------------------------------------------------

from resume_tailor.latex import rendered_lines  # noqa: E402


def test_rendered_lines_strips_latex_markup():
    # heavy markup but short visible text => 1 line
    s = r"\textbf{Backfilled 20K+} \hl{NCMEC} at \textbf{98\% precision}"
    assert rendered_lines(s) == 1


def test_rendered_lines_counts_bullets_separately():
    s = r"\item first bullet \item second bullet \item third"
    assert rendered_lines(s) == 3  # one line each (short)


def test_line_budget_allows_same_line_rewrite():
    orig = r"\item Built detection systems with strong testing culture."
    new = r"\item Built \hl{abuse detection} systems with robust testing."
    ok, why = is_safe_replacement(orig, new)
    assert ok, why


def test_line_budget_rejects_more_lines():
    # original: one short bullet (1 line). new: same bullet but ~3 lines of prose.
    orig = r"\item Built detection systems."
    new = r"\item " + ("Built detection systems and many more things " * 8)
    ok, why = is_safe_replacement(orig, new)
    assert not ok and "line" in why.lower()


def test_line_budget_paragraph_region():
    orig = "Finance Automation team using Kotlin for microservices."
    # roughly same length paragraph is fine
    new = r"\hl{Finance Automation} team building event-driven services in Kotlin."
    ok, why = is_safe_replacement(orig, new)
    assert ok, why


def test_rejects_changed_bullet_count():
    ok, why = is_safe_replacement(r"\item one \item two", r"\item only one")
    assert not ok and "bullet count" in why.lower()


def test_rejects_skills_collapsed_to_runon():
    orig = (r"\textbf{Core:} Python, Go. \textbf{Frameworks:} React, Helm. "
            r"\textbf{Domains:} Systems, Safety.")
    runon = "Python, Go, React, Helm, Systems, Safety, observability."
    ok, why = is_safe_replacement(orig, runon)
    assert not ok and "group" in why.lower()


def test_allows_skills_regrouped_keeping_labels():
    orig = (r"\textbf{Core:} Python, Go. \textbf{Frameworks:} React, Helm. "
            r"\textbf{Domains:} Systems, Safety.")
    new = (r"\textbf{Languages:} Python, Go. \textbf{Systems:} React, Helm. "
           r"\textbf{Safety:} Trust \& Safety, observability.")
    ok, why = is_safe_replacement(orig, new)
    assert ok, why


def test_bold_label_check_ignores_non_grouped_regions():
    # a normal bullet region (no label groups) is unaffected by the rule
    ok, why = is_safe_replacement(r"\item built systems", r"\item built \hl{safe} systems")
    assert ok, why
