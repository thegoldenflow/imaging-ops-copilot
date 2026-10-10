"""Eval run files (spec 6.6): `evals/runs/<timestamp>.json`, written by the one runner (app/aiops/evalrun.py).

A run file holds every agent evaluated in that run: its version, prompt version and prompt hash, model, mode, the
six common metrics, the module's own metrics and gates, the threshold, the verdict and the per-case results. This
module reads them (newest first, cached until the folder changes) and summarises them for the release gate, the
prompt gate and the AI Ops page.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

RUN_PREFIX = "run-"
LIVE_MODES = ("anthropic", "gemini")
_lock = threading.Lock()
_cache: dict = {"key": None, "checked": 0.0, "runs": []}
_override: list[Path] = []


def folder() -> Path:
    """evals/runs (EVAL_RUNS_DIR moves it; tests and scratch runs use `use_folder`). It sits next to the agents'
    eval sets, so AGENT_EVALS_DIR moves both."""
    if _override:
        return _override[-1]
    if os.environ.get("EVAL_RUNS_DIR"):
        return Path(os.environ["EVAL_RUNS_DIR"])
    from app.agents import evalsets

    return evalsets.root() / "runs"


class use_folder:
    """Tests: read and write run files under another folder."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def __enter__(self) -> Path:
        _override.append(self.path)
        invalidate()
        return self.path

    def __exit__(self, *exc) -> None:
        _override.pop()
        invalidate()


def invalidate() -> None:
    with _lock:
        _cache.update(key=None, checked=0.0)


def run_id_of(path: Path) -> str:
    return path.stem


def path_of(run_id: str) -> Path:
    name = run_id.removeprefix("evals/runs/").removesuffix(".json")
    if "/" in name or "\\" in name or not name.startswith(RUN_PREFIX):
        raise LookupError(f"{run_id!r} is not a run id (run-<timestamp>)")
    return folder() / f"{name}.json"


def load(run_id: str) -> dict:
    path = path_of(run_id)
    if not path.exists():
        raise LookupError(f"no run file {path.name} in {folder()}")
    return json.loads(path.read_text(encoding="utf-8"))


def _key(base: Path) -> tuple:
    try:
        entries = sorted((e.name, e.stat().st_mtime_ns) for e in os.scandir(base)
                         if e.name.startswith(RUN_PREFIX) and e.name.endswith(".json"))
    except FileNotFoundError:
        entries = []
    return (str(base), tuple(entries))


def summaries(max_age_s: float = 1.0) -> list[dict]:
    """Every run, newest first, without per-case results: {run_id, started_at, mode, variant, agents{...}}."""
    base = folder()
    now = time.monotonic()
    with _lock:
        if _cache["key"] is not None and _cache["key"][0] == str(base) and now - _cache["checked"] < max_age_s:
            return _cache["runs"]
    key = _key(base)
    with _lock:
        if key == _cache["key"]:
            _cache["checked"] = now
            return _cache["runs"]
    runs = []
    for name, _ in key[1]:
        try:
            data = json.loads((base / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        agents = {a: {k: v for k, v in entry.items() if k not in ("case_results",)}
                  for a, entry in (data.get("agents") or {}).items()}
        runs.append({"run_id": data.get("run_id") or Path(name).stem, "started_at": data.get("started_at", ""),
                     "finished_at": data.get("finished_at"), "mode": data.get("mode"),
                     "variant": data.get("variant"), "requested": data.get("requested"), "agents": agents})
    runs.sort(key=lambda r: (r["started_at"], r["run_id"]), reverse=True)
    with _lock:
        _cache.update(key=key, checked=now, runs=runs)
    return runs


def is_live(mode: str | None) -> bool:
    return mode in LIVE_MODES


def met(entry: dict, threshold: float | None) -> bool | None:
    """Did an agent's result meet the bar: task_success at or above the threshold and every module gate passed
    (None: the agent was not evaluated, e.g. no eval set or an error)."""
    if entry.get("status") in ("no_eval_set", "error", "skipped"):
        return None
    gates_ok = all(g.get("passed", False) for g in (entry.get("gates") or {}).values())
    success = (entry.get("metrics") or {}).get("task_success")
    if success is None:
        return gates_ok if entry.get("gates") else None
    bar = threshold if threshold is not None else entry.get("threshold")
    return gates_ok and bar is not None and success >= bar - 1e-9


def latest_for(agent_id: str, *, prompt_version: str | None = None, variants: bool = False) -> tuple[dict, dict] | None:
    """(run summary, agent entry) of the newest run that evaluated the agent (by default without a variant, and
    with this prompt version when given)."""
    for run in summaries():
        entry = run["agents"].get(agent_id)
        if entry is None or entry.get("status") in ("error", "skipped"):
            continue
        if not variants and (entry.get("variant") or run.get("variant")):
            continue
        if prompt_version is not None and entry.get("prompt_version") != prompt_version:
            continue
        return run, entry
    return None


def runs_for(agent_id: str) -> list[tuple[dict, dict]]:
    """Every run of one agent, newest first (variants included), for the comparison view."""
    return [(run, run["agents"][agent_id]) for run in summaries() if agent_id in run["agents"]
            and run["agents"][agent_id].get("status") not in ("skipped",)]
