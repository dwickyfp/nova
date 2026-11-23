"""Request-local access to an agent owner's skill library."""

from typing import Any

from app.modules.assistant.intelligence import SkillDefinition
from app.modules.assistant.skills import contains_credential_shape
from app.modules.assistant.tools import ToolInvocation, ToolOutcome
from app.modules.assistant.tools.load_skill import LoadSkillTool


class PersonalSkillLoader(LoadSkillTool):
    """An agent's ``load_skill``: only the skills its configuration makes available.

    Platform skills outside that set (the Console assistant's SQL-authoring
    playbooks) are never loaded by name, so the model cannot reach them.
    """

    def __init__(self, owner: str, definitions: dict[str, SkillDefinition]) -> None:
        self.owner = owner
        self.definitions = dict(definitions)
        # The schema lists only this agent's skills. Inheriting the platform
        # catalog let the model ask for playbooks it cannot load (live B8,
        # 2026-11-24), which spent the turn's repairs and ended it.
        names = sorted(self.definitions)
        self.parameters = {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    **({"enum": names} if names else {}),
                    "description": "Skill name to load, from this agent's available skills.",
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        }
        self.description = (
            "Load the full playbook for one of this agent's skills before answering a "
            "task it covers. Available skills: " + (", ".join(names) or "none") + "."
        )

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        name = invocation.arguments.get("name")
        definition = self.definitions.get(name) if isinstance(name, str) else None
        if definition and definition.trust_level == "user_skill":
            if getattr(context, "user_name", None) != self.owner:
                return ToolOutcome(ok=False, summary="", error="Skill not found.")
            if contains_credential_shape(definition.body):
                return ToolOutcome(
                    ok=False, summary="", error="Remove credentials from the skill before using it."
                )
            return ToolOutcome(ok=True, summary=definition.prompt_body())
        if definition is None:
            available = ", ".join(sorted(self.definitions)) or "none"
            # Recoverable: the model picks a listed skill or goes on without
            # one; a mistyped name must not end the person's turn.
            return ToolOutcome(
                ok=False,
                summary="",
                error=f"Unknown skill {name!r}. Available skills: {available}.",
                error_class="INVALID_TOOL_ARGUMENTS",
                recoverable=True,
            )
        return await super().run(invocation, context)
