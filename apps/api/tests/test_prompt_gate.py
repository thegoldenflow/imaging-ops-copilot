"""The prompt gate (spec 6.6): a prompt version change without an eval run that meets the agent's threshold fails.

The repository has no CI pipeline; this test is the gate (`uv run python -m app.aiops.promptgate` runs the same
check from the command line)."""

import json
import shutil

import pytest

from app.agents import prompts as prompt_files
from app.agents import registry
from app.aiops import evalrun, promptgate, release, runs

AGENT = "patient_message_triage"


def test_the_repository_prompts_pass_the_gate():
    """Every prompt file names a run in evals/runs that evaluated exactly its text; every active prompt met its
    agent's threshold in that run."""
    bad = promptgate.failures(promptgate.check())
    assert not bad, "\n".join(f"{f.version}: {f.problem}" for f in bad)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A copy of the prompts and an empty runs folder."""
    folder = tmp_path / "prompts"
    shutil.copytree(prompt_files.PROMPTS_DIR, folder)
    monkeypatch.setattr(prompt_files, "PROMPTS_DIR", folder)
    prompt_files._parse.cache_clear()
    with runs.use_folder(tmp_path / "runs") as run_dir:
        run_dir.mkdir()
        yield folder, run_dir
    prompt_files._parse.cache_clear()


def _run(run_dir, run_id, version, *, task_success, gates=None):
    spec = registry.require_agent(AGENT)
    entry = {"prompt_version": version, "prompt_sha256": evalrun.prompt_hash(version), "mode": "mock",
             "metrics": {"task_success": task_success}, "gates": gates or {}, "status": "met",
             "threshold": release.threshold(spec)}
    (run_dir / f"{run_id}.json").write_text(json.dumps({"run_id": run_id, "started_at": "2026-10-10T10:00:00",
                                                        "mode": "mock", "agents": {AGENT: entry}}), encoding="utf-8")
    runs.invalidate()


def _new_version(folder, n: int, text_change: str) -> str:
    v1 = (folder / AGENT / "1.md").read_text(encoding="utf-8")
    body = v1.split("\n---\n", 1)[1].replace("## template\n", f"## template\n{text_change}\n", 1)
    header = "author: tests\nchange_note: a change\napproved_by: tests\neval_run: none\n---\n"
    (folder / AGENT / f"{n}.md").write_text(header + body, encoding="utf-8")
    prompt_files._parse.cache_clear()
    return f"{AGENT}@{n}"


def _active(version: str):
    spec = registry.require_agent(AGENT)
    return registry.temporary(AGENT, spec.model_copy(update={"prompt_version": version}))


def test_a_new_prompt_version_without_an_eval_run_fails_the_gate(sandbox):
    folder, run_dir = sandbox
    version = _new_version(folder, 3, "Answer briefly.")
    spec = registry.require_agent(AGENT)
    finding = promptgate.check_version(version, spec, active=True)
    assert not finding.ok and "no eval run id" in finding.problem
    with _active(version):  # the pointer moved to the new version: the whole gate fails
        assert any(f.version == version and not f.ok for f in promptgate.failures(promptgate.check()))


def test_an_eval_run_below_the_threshold_fails_the_active_version_but_a_candidate_may_wait(sandbox):
    folder, run_dir = sandbox
    version = _new_version(folder, 3, "Answer briefly.")
    threshold = release.threshold(registry.require_agent(AGENT))
    _run(run_dir, "run-2026-10-10T100000", version, task_success=threshold - 0.1)
    promptgate.set_eval_run(version, "run-2026-10-10T100000")
    spec = registry.require_agent(AGENT)
    assert promptgate.check_version(version, spec, active=False).ok  # a candidate with its run (comparison view)
    active = promptgate.check_version(version, spec, active=True)
    assert not active.ok and "below the threshold" in active.problem
    # a module gate failing blocks too
    _run(run_dir, "run-2026-10-10T110000", version, task_success=1.0,
         gates={"recall": {"value": 0.2, "threshold": 0.8, "passed": False}})
    promptgate.set_eval_run(version, "run-2026-10-10T110000")
    assert "gates failed: recall" in promptgate.check_version(version, spec, active=True).problem
    # a run that met the threshold lets the version become active
    _run(run_dir, "run-2026-10-10T120000", version, task_success=threshold + 0.05)
    promptgate.set_eval_run(version, "run-2026-10-10T120000")
    assert promptgate.check_version(version, spec, active=True).ok


def test_editing_a_prompt_after_its_run_fails_until_it_is_evaluated_again(sandbox):
    folder, run_dir = sandbox
    version = _new_version(folder, 3, "Answer briefly.")
    threshold = release.threshold(registry.require_agent(AGENT))
    _run(run_dir, "run-2026-10-10T100000", version, task_success=threshold + 0.05)
    promptgate.set_eval_run(version, "run-2026-10-10T100000")
    spec = registry.require_agent(AGENT)
    assert promptgate.check_version(version, spec, active=True).ok
    path = folder / AGENT / "3.md"
    path.write_text(path.read_text(encoding="utf-8").replace("Answer briefly.", "Answer very briefly."),
                    encoding="utf-8")
    prompt_files._parse.cache_clear()
    finding = promptgate.check_version(version, spec, active=True)
    assert not finding.ok and "changed after" in finding.problem


def test_a_rollback_is_moving_the_pointer_back(sandbox):
    folder, run_dir = sandbox
    version = _new_version(folder, 3, "Answer briefly.")
    _run(run_dir, "run-2026-10-10T100000", version, task_success=0.1)
    promptgate.set_eval_run(version, "run-2026-10-10T100000")
    v1 = f"{AGENT}@1"
    threshold = release.threshold(registry.require_agent(AGENT))
    _run(run_dir, "run-2026-10-10T090000", v1, task_success=threshold + 0.01)
    promptgate.set_eval_run(v1, "run-2026-10-10T090000")
    with _active(version):
        assert not promptgate.check_version(version, registry.require_agent(AGENT), active=True).ok
    # back on version 1 (the one-line change in config/agents/<agent>.yaml): its run still matches
    assert registry.require_agent(AGENT).prompt_version == v1
    assert promptgate.check_version(v1, registry.require_agent(AGENT), active=True).ok


def test_the_gate_script_exits_1_on_a_failing_prompt(sandbox, capsys):
    folder, _ = sandbox
    version = _new_version(folder, 3, "Answer briefly.")
    with _active(version):
        assert promptgate.main([]) == 1
    assert "FAIL" in capsys.readouterr().out
