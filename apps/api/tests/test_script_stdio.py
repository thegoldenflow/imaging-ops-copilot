"""The scripts write UTF-8 when their output is redirected (the 2026-10-12 live run: GBK could not encode "SpO₂")."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _print_spo2(*, helper: bool) -> subprocess.CompletedProcess:
    code = ("import sys; sys.path.insert(0, sys.argv[1]); from _stdio import utf8_stdio; utf8_stdio(); "
            if helper else "") + "print('SpO₂ 94%')"
    # PYTHONIOENCODING=gbk: what a Chinese-locale Windows machine uses for a redirected stdout
    return subprocess.run([sys.executable, "-c", code, str(SCRIPTS)], capture_output=True,
                          env={**os.environ, "PYTHONIOENCODING": "gbk", "PYTHONUTF8": "0"})


def test_a_redirected_gbk_stdout_writes_utf8_after_the_helper():
    assert b"UnicodeEncodeError" in _print_spo2(helper=False).stderr  # the failure the helper fixes
    done = _print_spo2(helper=True)
    assert done.returncode == 0, done.stderr
    assert done.stdout.decode("utf-8").strip() == "SpO₂ 94%"


def test_every_script_that_prints_calls_the_helper_first_in_its_main_block():
    missing = [p.name for p in sorted(SCRIPTS.glob("*.py")) if p.name != "_stdio.py"
               and "print(" in (text := p.read_text(encoding="utf-8"))
               and 'if __name__ == "__main__":\n    from _stdio import utf8_stdio\n\n    utf8_stdio()\n' not in text]
    assert not missing


# Review finding 4: the `python -m` entry points that print must configure UTF-8 too, not only scripts/*.py.
MODULE_MAINS = ["app/aiops/evalrun.py", "app/aiops/promptgate.py", "app/modules/evals/run.py",
                "app/modules/evals/build.py", "app/modules/clinical_kg/terms.py", "app/core/db/crypto.py",
                "app/seed.py"]


def test_app_core_stdio_writes_utf8_under_a_redirected_gbk_stdout():
    api = SCRIPTS.parent
    code = ("import sys; sys.path.insert(0, sys.argv[1]); from app.core.stdio import utf8_stdio; "
            "utf8_stdio(); print('SpO\u2082 94%')")
    done = subprocess.run([sys.executable, "-c", code, str(api)], capture_output=True,
                          env={**os.environ, "PYTHONIOENCODING": "gbk", "PYTHONUTF8": "0"})
    assert done.returncode == 0, done.stderr
    assert done.stdout.decode("utf-8").strip() == "SpO\u2082 94%"


def test_every_python_m_entry_point_that_prints_configures_utf8_first_in_its_main_block():
    api = SCRIPTS.parent
    pattern = 'if __name__ == "__main__":\n    from app.core.stdio import utf8_stdio\n\n    utf8_stdio()\n'
    missing = [rel for rel in MODULE_MAINS if pattern not in (api / rel).read_text(encoding="utf-8")]
    assert not missing
