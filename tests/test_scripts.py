"""Smoke tests for the shell scripts: syntax validity + basic contracts.
These guard against accidental breakage of install/uninstall/launcher without
running a full (destructive) install."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ["install.sh", "uninstall.sh", "resume-tailor"]

bash = shutil.which("bash")


@pytest.mark.skipif(not bash, reason="bash not available")
@pytest.mark.parametrize("script", SCRIPTS)
def test_script_syntax_valid(script):
    """`bash -n` parses without syntax errors."""
    p = ROOT / script
    assert p.exists(), f"{script} missing"
    res = subprocess.run([bash, "-n", str(p)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


@pytest.mark.parametrize("script", SCRIPTS)
def test_script_executable(script):
    assert (ROOT / script).stat().st_mode & 0o111, f"{script} not executable"


def test_install_help_runs():
    """`install.sh --help` exits cleanly without doing any work."""
    if not bash:
        pytest.skip("bash not available")
    res = subprocess.run([bash, str(ROOT / "install.sh"), "--help"],
                         capture_output=True, text=True, timeout=30)
    assert res.returncode == 0
    assert "usage" in (res.stdout + res.stderr).lower()


def test_launcher_errors_without_venv(tmp_path):
    """Launcher copied somewhere with no .venv reports a clear error, not a crash."""
    if not bash:
        pytest.skip("bash not available")
    fake = tmp_path / "resume-tailor"
    fake.write_text((ROOT / "resume-tailor").read_text())
    fake.chmod(0o755)
    res = subprocess.run([bash, str(fake), "http://x"],
                         capture_output=True, text=True, timeout=30)
    assert res.returncode != 0
    assert "virtualenv missing" in (res.stdout + res.stderr).lower()


def test_gitignore_protects_secrets():
    """Critical: secrets/venv/active-config must be gitignored."""
    gi = (ROOT / ".gitignore").read_text()
    for pat in (".env.local", ".venv", "resume-tailor.toml", "out/"):
        assert pat in gi, f"{pat} not in .gitignore"
