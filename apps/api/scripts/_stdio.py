"""UTF-8 stdout and stderr for the scripts.

Redirected to a file or a pipe, Python writes stdout and stderr in the locale's code page; on a Windows machine with
a Chinese locale that is GBK, and a line with "SpO₂" or a Chinese instruction raises UnicodeEncodeError (seen on the
2026-10-12 live run, evals/live-run-2026-10-12.md). Each script calls `utf8_stdio()` first in its `__main__` block
(not at import: the eval runner also loads some scripts in the API process). `PYTHONUTF8=1` does the same from
outside.
"""

from __future__ import annotations

import sys


def utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)  # None: no stream (pythonw) or a replaced one
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
