"""``load_skill`` — the assistant's read-only skill loader.

A skill is a validated playbook for one kind of task, authored from
``docs/sql_docs/``. The catalog is already in the system prompt; this tool lets
the model pull one body on demand, so the prompt stays small and the model pays
for a skill only when a task needs it.

This tool is **pure**: it reads packaged, credential-free data and touches no
database, no user connection, and no network. It is always ``read_only``, so a
conversation grant may auto-approve it, and it never appears as a destructive
action in the panel.
"""

from __future__ import annotations

from typing import Any

from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.skill_registry import skill_library
from app.modules.assistant.tools import ToolInvocation, ToolOutcome

_TOOL_PARAMETERS = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "enum": skill_library.names(),
            "description": (
                "Skill name to load, e.g. create-table, debug-sql, create-ml-model. "
                "Use a name from the available-skills catalog."
            ),
        },
    },
    "required": ["name"],
    "additionalProperties": False,
}


class LoadSkillTool:
    """Loads one skill body by name. Read-only and side-effect free."""

    name = "load_skill"
    description = (
        "Load the full playbook for one Nova SQL skill (for example create-table, "
        "debug-sql, create-ml-model, stage-query) before answering a task it "
        "covers. The catalog lists the available skill names."
    )
    parameters = _TOOL_PARAMETERS
    classification: ToolClassification = "read_only"
    #: Reading packaged, credential-free playbooks is not an action on the user's
    #: data, so it never prompts. Only ``query_execute`` needs approval.
    requires_consent = False

    def preview(self, invocation: ToolInvocation) -> str:
        skill_name = _name_from(invocation)
        return f"load skill `{skill_name}`" if skill_name else "load skill"

    async def run(self, invocation: ToolInvocation, context: Any) -> ToolOutcome:
        del context  # No user connection: the library is packaged read-only data.
        skill_name = _name_from(invocation)
        if not skill_name:
            return ToolOutcome(
                ok=False, summary="", error="No skill name was provided."
            )
        try:
            body = skill_library.load(skill_name)
        except KeyError:
            available = ", ".join(skill_library.names())
            return ToolOutcome(
                ok=False,
                summary="",
                error=f"Unknown skill {skill_name!r}. Available skills: {available}.",
            )
        return ToolOutcome(ok=True, summary=body)


def _name_from(invocation: ToolInvocation) -> str:
    value = invocation.arguments.get("name")
    return value.strip() if isinstance(value, str) else ""


#: Process-wide instance, registered by ``app.modules.assistant.registry``.
load_skill_tool = LoadSkillTool()
