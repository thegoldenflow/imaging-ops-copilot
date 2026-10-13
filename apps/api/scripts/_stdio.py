"""UTF-8 stdout/stderr for the scripts. Canonical implementation: apps/api/app/core/stdio.py.

Most scripts put apps/api on sys.path (at import time) and so can import the canonical helper. The few generator
scripts that never import `app` (e.g. gen_deid_eval.py, gen_med_safety_eval.py) fall back to the inline copy below.
See app/core/stdio.py for why this exists (GBK on redirected stdout) and why it is called from `__main__`.
"""

from __future__ import annotations

try:
    from app.core.stdio import utf8_stdio  # noqa: F401
except Exception:  # a script that does not put apps/api on sys.path / does not import app
    import sys

    def utf8_stdio() -> None:
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure is not None:
                reconfigure(encoding="utf-8", errors="replace")
