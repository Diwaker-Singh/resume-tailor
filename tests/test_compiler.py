"""LaTeX compiler wrapper. Engine-dependent tests skip when no engine present."""
import shutil

import pytest

from resume_tailor.compiler import CompileError, _find_engine, compile_tex

from resume_tailor.compiler import has_engine
_HAS_ENGINE = has_engine()

_MINIMAL_TEX = (
    "\\documentclass{article}\n\\begin{document}\nHello tailored world.\n"
    "\\end{document}\n"
)


def test_find_engine_when_present():
    if not _HAS_ENGINE:
        pytest.skip("no LaTeX engine installed")
    name, cmd = _find_engine()
    assert name in ("tectonic", "latexmk", "pdflatex")
    assert isinstance(cmd, list) and cmd


@pytest.mark.skipif(not _HAS_ENGINE, reason="no LaTeX engine installed")
def test_compile_minimal_doc(tmp_path):
    tex = tmp_path / "doc.tex"
    tex.write_text(_MINIMAL_TEX)
    pdf = compile_tex(tex, tmp_path)
    assert pdf.exists()
    assert pdf.suffix == ".pdf"
    assert pdf.stat().st_size > 0


@pytest.mark.skipif(not _HAS_ENGINE, reason="no LaTeX engine installed")
def test_compile_latex_error_raises_even_if_pdf_emitted(tmp_path):
    # \undefinedmacro logs a "! " error; pdflatex may still emit a PDF, but a
    # broken render must NOT pass silently — compile_tex should raise.
    tex = tmp_path / "doc.tex"
    tex.write_text(
        "\\documentclass{article}\\begin{document}"
        "ok \\someundefinedmacro more\\end{document}"
    )
    with pytest.raises(CompileError):
        compile_tex(tex, tmp_path)


@pytest.mark.skipif(not _HAS_ENGINE, reason="no LaTeX engine installed")
def test_compile_fatal_error_raises(tmp_path):
    # missing \end{document} + bad input => no PDF => CompileError
    tex = tmp_path / "doc.tex"
    tex.write_text("\\documentclass{article}\\begin{document}\\end{nope}")
    with pytest.raises(CompileError):
        compile_tex(tex, tmp_path)
