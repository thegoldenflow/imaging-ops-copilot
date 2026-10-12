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
