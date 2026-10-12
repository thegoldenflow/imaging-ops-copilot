"""The prompt gate (spec 6.6): a prompt change cannot go in without an eval run that meets the agent's threshold.

    cd apps/api && uv run python -m app.aiops.promptgate            # exit 1 when a prompt fails the gate
    cd apps/api && uv run python -m app.aiops.promptgate --strict   # also fail prompts of agents without an eval set

Every prompt file `apps/api/prompts/<agent>/<n>.md` names in its header line `eval_run:` the run that evaluated it
(`evals/runs/<run id>.json`, written by `uv run python -m app.aiops.evalrun`; `--stamp` fills the line in). The gate
checks, for every prompt file:

1. the run exists and evaluated this prompt version of this agent;
2. with the same text: the run recorded the prompt's hash, so editing a prompt after its run fails;
3. for the version the agent's registry entry points at (`prompt_version`, the one in use): the run met the agent's
   threshold (`eval_threshold`, the release gate's bar; task_success and the module's gates). A candidate version
   needs a run (the comparison view shows it) but may be below the bar until it is made active.

A rollback is moving the pointer back to the previous version: its run still matches, so the gate passes. The
repository has no CI pipeline, so the backend test suite runs the gate (tests/test_prompt_gate.py); adding a CI
provider would be a stack change for the owner to confirm.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import asdict, dataclass

from app.agents import prompts as prompt_files
from app.aiops import release, runs


@dataclass
class Finding:
    version: str
    agent_id: str
    active: bool
    ok: bool
    problem: str | None
    eval_run: str | None
    task_success: float | None = None
    threshold: float | None = None
    ungated: bool = False


def _run_id(value: str | None) -> str | None:
    if not value:
        return None
    m = re.search(r"(run-[0-9T:\-]+(?:-\d+)?)", value)
    return m.group(1) if m else None


def check_version(version: str, spec, *, active: bool) -> Finding:
    from app.aiops.evalrun import prompt_hash

    agent_id = version.partition("@")[0]
    try:
        header = prompt_files.header(version)
    except prompt_files.PromptFileError as e:
        return Finding(version, agent_id, active, False, str(e), None)
    eval_run = header.get("eval_run")
    run_id = _run_id(eval_run)
    if run_id is None:
        return Finding(version, agent_id, active, False, f"no eval run id in the header (eval_run: {eval_run})",
                       eval_run)
    try:
        record = runs.load(run_id)
    except LookupError as e:
        return Finding(version, agent_id, active, False, str(e), run_id)
    entry = (record.get("agents") or {}).get(agent_id)
    if entry is None or entry.get("prompt_version") != version:
        return Finding(version, agent_id, active, False, f"{run_id} did not evaluate {version}", run_id)
    if entry.get("prompt_sha256") != prompt_hash(version):
        return Finding(version, agent_id, active, False,
                       f"{version} changed after {run_id} (prompt hash differs): run the eval again", run_id)
    threshold = release.threshold(spec) if spec is not None else None
    success = (entry.get("metrics") or {}).get("task_success")
    if entry.get("status") == "no_eval_set":
        return Finding(version, agent_id, active, True, "no eval set: the text is pinned to its run, not judged",
                       run_id, None, threshold, ungated=True)
    if entry.get("status") == "error":
        return Finding(version, agent_id, active, False, f"{run_id}: the eval failed to run", run_id)
    if active:
        verdict = runs.met(entry, threshold)
        if not verdict:
            failed = [k for k, g in (entry.get("gates") or {}).items() if not g.get("passed")]
            why = (f"task_success {success} below the threshold {threshold}" if success is not None and
                   success < (threshold or 0) else f"gates failed: {', '.join(failed) or 'none recorded'}")
            return Finding(version, agent_id, active, False, f"{run_id}: {why}", run_id, success, threshold)
    return Finding(version, agent_id, active, True, None, run_id, success, threshold)


def check(strict: bool = False) -> list[Finding]:
    """Every prompt file of every registered agent."""
    from app.agents import registry

    out = []
    for agent_id, spec in sorted(registry.agents().items()):
        versions = prompt_files.versions(agent_id)
        if spec.prompt_version and spec.prompt_version not in versions:
            out.append(Finding(spec.prompt_version, agent_id, True, False, "the registry points at a prompt file "
                                                                           "that does not exist", None))
        for version in versions:
            f = check_version(version, spec, active=version == spec.prompt_version)
            if strict and f.ungated and f.active:
                f.ok, f.problem = False, "no eval set (strict mode)"
            out.append(f)
    return out


def failures(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if not f.ok]


def set_eval_run(version: str, run_id: str) -> None:
    """Write the run id into a prompt file's `eval_run:` header line."""
    path = prompt_files.path_for(version)
    text = path.read_text(encoding="utf-8")
    new, n = re.subn(r"(?m)^eval_run:.*$", f"eval_run: evals/runs/{run_id}.json", text, count=1)
    if n != 1:
        raise prompt_files.PromptFileError(f"{path}: no eval_run header line")
    path.write_text(new, encoding="utf-8", newline="\n")
    prompt_files._parse.cache_clear()


def report(findings: list[Finding]) -> list[dict]:
    return [asdict(f) for f in findings]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prompt gate (spec 6.6)")
    parser.add_argument("--strict", action="store_true", help="fail active prompts of agents without an eval set")
    args = parser.parse_args(argv)
    import app.core.config  # noqa: F401

    findings = check(strict=args.strict)
    for f in findings:
        mark = "ok  " if f.ok else "FAIL"
        state = "active" if f.active else "candidate"
        print(f"{mark} {f.version:34} {state:9} {f.eval_run or '-':34} {f.problem or ''}")
    bad = failures(findings)
    print(f"{len(findings)} prompt versions, {len(bad)} failing the gate")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
