"""Versioned prompt files for runtime agents: `apps/api/prompts/<agent_id>/<n>.md` (spec 6.3, 6.6).

A file starts with the four header lines of 6.6 (`author`, `change_note`,
`approved_by`, `eval_run`), then `---`, then a `## system` and a `## template`
section. The version is `<agent_id>@<n>`; the agent registry's `prompt_version`
points at the one in use, so a rollback is a one-line registry change.
`{{UNTRUSTED_RULE}}` in the system section is replaced by the runtime's rule for
untrusted text (app/agents/untrusted.py). The imaging agents keep their prompts
in code (their `Prompt` constants); the registry records those versions too.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from app.agents.untrusted import UNTRUSTED_RULE
from app.llm.prompts import Prompt

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
HEADER = ("author", "change_note", "approved_by", "eval_run")


class PromptFileError(ValueError):
    pass


def path_for(version: str) -> Path:
    agent_id, _, n = version.partition("@")
    if not agent_id or not n.isdigit():
        raise PromptFileError(f"prompt version {version!r} is not <agent_id>@<n>")
    return PROMPTS_DIR / agent_id / f"{n}.md"


def header(version: str) -> dict[str, str]:
    return _parse(version)[0]


@cache
def _parse(version: str) -> tuple[dict[str, str], str, str]:
    path = path_for(version)
    if not path.exists():
        raise PromptFileError(f"no prompt file {path.name} for {version}")
    head, sep, body = path.read_text(encoding="utf-8").partition("\n---\n")
    if not sep:
        raise PromptFileError(f"{path}: the header ends with a line '---'")
    fields = dict(line.split(":", 1) for line in head.strip().splitlines() if ":" in line)
    fields = {k.strip(): v.strip() for k, v in fields.items()}
    if tuple(fields) != HEADER:
        raise PromptFileError(f"{path}: the header lines are {', '.join(HEADER)}")
    _, sep_s, rest = body.partition("## system\n")
    system, sep_t, template = rest.partition("## template\n")
    if not (sep_s and sep_t):
        raise PromptFileError(f"{path}: needs a '## system' and a '## template' section")
    return fields, system.strip().replace("{{UNTRUSTED_RULE}}", UNTRUSTED_RULE), template.strip()


def load(version: str) -> Prompt:
    _, system, template = _parse(version)
    return Prompt(name=version.partition("@")[0], version=version, system=system, template=template)
