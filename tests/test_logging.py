"""Logging setup: level selection + logger naming."""
import logging

from resume_tailor import logging_setup


def _reset():
    logging_setup._CONFIGURED = False
    logging.getLogger("resume_tailor").handlers.clear()


def test_configure_sets_warning_by_default():
    _reset()
    logging_setup.configure(0)
    assert logging.getLogger("resume_tailor").level == logging.WARNING


def test_configure_verbose_levels():
    _reset(); logging_setup.configure(1)
    assert logging.getLogger("resume_tailor").level == logging.INFO
    _reset(); logging_setup.configure(2)
    assert logging.getLogger("resume_tailor").level == logging.DEBUG


def test_env_overrides_verbosity(monkeypatch):
    _reset()
    monkeypatch.setenv("RESUME_TAILOR_LOG_LEVEL", "ERROR")
    logging_setup.configure(2)  # would be DEBUG, but env wins
    assert logging.getLogger("resume_tailor").level == logging.ERROR


def test_get_logger_namespaced():
    log = logging_setup.get_logger("llm.gemini")
    assert log.name == "resume_tailor.llm.gemini"
