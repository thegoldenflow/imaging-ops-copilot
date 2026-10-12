"""Module READMEs (spec 6.3 "four fixed sections", WP10) and the newcomer docs' links.

Every hospital module and platform piece, and the imaging agents together, has a README with the four sections in
order: input resources, output schema, eval set and threshold, known failure modes. Every agent with a model is named
in one README's release-gate line with the threshold its registry entry gives, so a README cannot quote a stale bar.
"""

import re
from pathlib import Path

import pytest

from app.agents import registry
from app.aiops import release

API = Path(__file__).resolve().parents[1]
REPO = API.parents[1]
SECTIONS = ["## Input resources", "## Output schema", "## Eval set and threshold", "## Known failure modes"]
MODULE_READMES = [
    "apps/api/app/agents/README.md",  # agent runtime and Tool Gateway (6.4)
    "apps/api/app/aiops/README.md",  # AI Ops (6.6)
    "apps/api/app/ehr/README.md",  # EHR platform: gateway, events, simulator, access, consent, signing (6.1-6.3)
    "apps/api/app/llm/README.md",  # LLM call layer and de-identification
    "apps/api/app/workflows/README.md",  # Temporal workflows (6.5)
    "apps/api/app/modules/control_tower/README.md",  # 7.1
    "apps/api/models/flow/README.md",  # 7.1 flow models
    "apps/api/app/modules/med_safety/README.md",  # 7.2
    "apps/api/app/modules/documentation/README.md",  # 7.3
    "apps/api/app/modules/voice_services/README.md",  # 7.4
    "apps/api/app/modules/storyline/README.md",  # 8.2
    "apps/api/app/modules/roadmap/README.md",  # 8.1, 8.3 and the three stubs
    "apps/api/app/modules/README.md",  # the imaging centre's agents (phases 1-4)
]
EVAL_READMES = sorted(str(p.relative_to(REPO)).replace("\\", "/") for p in (REPO / "evals").glob("*/README.md")
                      if p.parent.name not in ("runs", "storyline"))
NEWCOMER_DOCS = ["README.md", "docs/README.md", "demo/README.md", "evals/README.md", "docs/interview-narrative.md",
                 "docs/safety-tiers.md", "docs/DEPLOY.md"]


@pytest.mark.parametrize("rel", MODULE_READMES + EVAL_READMES)
def test_four_fixed_sections_in_order(rel):
    headings = [ln.strip() for ln in (REPO / rel).read_text(encoding="utf-8").splitlines() if ln.startswith("## ")]
    assert [h for h in headings if h in SECTIONS] == SECTIONS, f"{rel}: {headings}"


def test_every_agent_has_its_registry_threshold_in_a_module_readme():
    quoted: dict[str, tuple[str, float]] = {}
    for rel in MODULE_READMES:
        paragraphs = re.split(r"\n\s*\n", (REPO / rel).read_text(encoding="utf-8"))
        for paragraph in (p for p in paragraphs if p.startswith("Release gate (6.6):")):
            for agent_id, value in re.findall(r"`(\w+)` eval_threshold ([0-9.]+)", " ".join(paragraph.split())):
                assert agent_id not in quoted, f"{agent_id} in {quoted.get(agent_id)} and {rel}"
                quoted[agent_id] = (rel, float(value))
    agents = {a: s for a, s in registry.agents().items() if s.is_agent}
    assert sorted(quoted) == sorted(agents), set(agents) ^ set(quoted)
    for agent_id, (rel, value) in quoted.items():
        assert value == pytest.approx(release.threshold(agents[agent_id])), f"{rel}: {agent_id}"


def _links(text: str) -> list[str]:
    return [m for m in re.findall(r"\]\(([^)\s]+)\)", text) if not re.match(r"[a-z]+:", m) and not m.startswith("#")]


@pytest.mark.parametrize("rel", NEWCOMER_DOCS + MODULE_READMES)
def test_relative_links_resolve(rel):
    path = REPO / rel
    missing = [link for link in _links(path.read_text(encoding="utf-8"))
               if not (path.parent / link.split("#", 1)[0]).exists()]
    assert not missing, f"{rel}: {missing}"
