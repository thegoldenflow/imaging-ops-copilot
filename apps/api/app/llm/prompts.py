"""Versioned prompt templates. Every LLM call records the version it used."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    system: str
    template: str

    def render(self, **variables) -> str:
        return self.template.format(**variables)


class RegisteredPrompt:
    """The prompt an embedded agent's registry entry points at, resolved at every use: moving the pointer in
    config/agents/<agent_id>.yaml switches (or rolls back) the prompt without a code change or a restart."""

    def __init__(self, agent_id: str) -> None:
        self.name = agent_id

    def _current(self) -> Prompt:
        from app.agents import registry
        from app.agents.prompts import PromptFileError, load

        spec = registry.require_agent(self.name)
        if not spec.prompt_version:
            raise PromptFileError(f"{self.name} has no prompt_version in the agent registry")
        return load(spec.prompt_version)

    @property
    def version(self) -> str:
        return self._current().version

    @property
    def system(self) -> str:
        return self._current().system

    @property
    def template(self) -> str:
        return self._current().template

    def render(self, **variables) -> str:
        return self._current().render(**variables)

    def __repr__(self) -> str:
        return f"RegisteredPrompt({self.name!r})"


def registered(agent_id: str) -> RegisteredPrompt:
    return RegisteredPrompt(agent_id)


# The prompts live in apps/api/prompts/<agent>/<n>.md (6.6); these names follow the registry pointer.
CXR_DRAFT = registered("cxr_draft")

VOICE_AGENT = registered("voice_agent")

CALL_SUMMARY = registered("call_summary")
