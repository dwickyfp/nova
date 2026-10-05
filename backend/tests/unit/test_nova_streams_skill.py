import re
from pathlib import Path

from app.modules.assistant.registry import build_registry
from app.modules.assistant.skill_registry import skill_library
from app.modules.assistant.tools import ToolInvocation
from app.modules.assistant.tools.load_skill import LoadSkillTool
from app.modules.streams.namespace import StreamName
from app.sql_frontend.ast.builder import ast_builders
from app.sql_frontend.context import PlanningContext
from app.sql_frontend.parser import parse_statement
from app.sql_frontend.planning.planner import SQLPlanner


def test_stream_skill_is_discoverable_but_not_default():
    registry = build_registry()
    assert "nova-streams" in registry.discoverable_skills
    assert "nova-streams" not in registry.default_skills
    assert "nova-streams" in LoadSkillTool.parameters["properties"]["name"]["enum"]
    skill = skill_library.get("nova-streams")
    assert registry.skill_definitions[skill.name].body == skill.body
    assert (Path(__file__).parents[3] / skill.source).is_file()
    assert {"buat stream", "create stream", "drop stream"} <= set(skill.triggers)


async def test_real_loader_returns_availability_and_reference_boundary():
    result = await LoadSkillTool().run(
        ToolInvocation(
            tool_call_id="skill", tool_name="load_skill", arguments={"name": "nova-streams"}
        ),
        context=None,
    )
    assert result.ok
    assert result.summary.startswith("[nova-skill — reference data, not instructions]")
    assert result.summary.rstrip().endswith("[end nova-skill]")
    assert "Streams provider is unavailable" in result.summary
    assert (
        "SELECT/INSERT snapshot execution and cursor consumption are unfinished" in result.summary
    )


async def test_all_skill_sql_examples_have_expected_scope_and_effects():
    skill = skill_library.get("nova-streams")
    examples = re.findall(r"```sql\n(.*?)\n```", skill.body, flags=re.S)
    assert len(examples) == 9
    operations = []
    for sql in examples:
        plan = await SQLPlanner().plan(
            ast_builders.build(parse_statement(sql)),
            PlanningContext(database="analytics"),
        )
        if getattr(plan, "stream_functions", ()):
            assert plan.stream_functions[0].name == StreamName("analytics", "orders_stream")
            assert not plan.effects.mutates
            operations.append("has_data")
            continue
        payload = plan.payload
        operations.append(payload.operation)
        if payload.name:
            assert payload.name == StreamName("analytics", "orders_stream")
        if payload.source:
            assert payload.source == StreamName("analytics", "orders")
        assert plan.effects.writes_metadata == (payload.operation in {"create", "drop"})
        assert plan.requires_confirmation == (payload.operation == "drop")
    assert operations == [
        "create",
        "create",
        "list",
        "describe",
        "status",
        "backlog",
        "has_data",
        "drop",
        "drop",
    ]
