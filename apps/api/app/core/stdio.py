"""UTF-8 stdout and stderr for command-line entry points.

Redirected to a file or a pipe, Python encodes stdout/stderr with the locale's code page; on a Chinese-locale
Windows machine that is GBK, and a line with "SpO₂" or a Chinese string raises
`UnicodeEncodeError: 'gbk' codec can't encode character ...` (seen on the 2026-10-12 live run,
evals/live-run-2026-10-12.md). Call `utf8_stdio()` as the first thing in a `__main__` block (not at import time:
some of these modules are imported in-process by the eval runner, and reconfiguring the host's streams then would
be a side effect). `PYTHONUTF8=1` does the same from outside.

scripts/_stdio.py re-exports this for the scripts (falling back to a copy for the few that do not put apps/api on
sys.path).
"""

from __future__ import annotations

import sys


def utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)  # None: no stream (pythonw) or a replaced one
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
