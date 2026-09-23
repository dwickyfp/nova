"""Request-local access to an agent owner's skill library."""

from typing import Any

from app.modules.assistant.intelligence import SkillDefinition
from app.modules.assistant.skills import contains_credential_shape
from app.modules.assistant.tools import ToolInvocation, ToolOutcome
from app.modules.assistant.tools.load_skill import LoadSkillTool


class PersonalSkillLoader(LoadSkillTool):
    def __init__(self, owner: str, definitions: dict[str, SkillDefinition]) -> None:
        self.owner = owner
        self.definitions = dict(definitions)

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
        return await super().run(invocation, context)
