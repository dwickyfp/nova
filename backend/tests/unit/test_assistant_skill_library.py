"""Unit tests for the curated assistant skill library and its loader tool.

The library is packaged read-only data; these tests lock in that every skill
parses, that names match filenames, that a credential-shaped body is refused,
and that the ``load_skill`` tool surfaces a clear error for an unknown name.
"""

from __future__ import annotations

import pytest

from app.modules.assistant.skill_registry import (
    SkillLibraryError,
    skill_library,
)
from app.modules.assistant.skills import contains_credential_shape
from app.modules.assistant.tools import ToolInvocation
from app.modules.assistant.tools.load_skill import LoadSkillTool

EXPECTED_SKILLS = {
    "accountadmin-guardrail",
    "ai-functions",
    "copy-into",
    "create-ml-model",
    "create-table",
    "create-task",
    "create-user",
    "debug-sql",
    "scope-boundary",
    "stage-query",
    "writing-style",
}


def test_library_loads_every_expected_skill():
    assert set(skill_library.names()) == EXPECTED_SKILLS


def test_every_skill_has_catalog_metadata():
    for skill in skill_library.skills:
        assert skill.title
        assert skill.summary
        assert skill.triggers, f"{skill.name} has no triggers"
        assert skill.body.strip()


def test_catalog_prompt_lists_every_skill_by_name():
    catalog = skill_library.catalog_prompt()
    for name in EXPECTED_SKILLS:
        assert f"`{name}`" in catalog


def test_load_returns_a_delimited_data_excerpt():
    body = skill_library.load("create-table")
    assert body.startswith("[nova-skill")
    assert body.rstrip().endswith("[end nova-skill]")
    assert "CREATE TABLE" in body


def test_load_unknown_skill_raises_key_error():
    with pytest.raises(KeyError):
        skill_library.load("does-not-exist")


def test_no_skill_body_carries_a_credential_shape():
    # The library must stay credential-free; the same screen the prompt assembler
    # uses is applied on load, so this is a belt-and-braces assertion on disk.
    for skill in skill_library.skills:
        assert not contains_credential_shape(skill.body), skill.name


def test_skill_name_must_match_filename(tmp_path, monkeypatch):
    from app.modules.assistant import skill_registry as module

    (tmp_path / "alpha.md").write_text(
        "---\nname: beta\ntitle: T\nsummary: S\n---\n\nbody\n", encoding="utf-8"
    )
    monkeypatch.setattr(module, "_SKILL_DIR", tmp_path)
    with pytest.raises(SkillLibraryError):
        module.SkillLibrary()


def test_skill_with_credential_shape_is_refused(tmp_path, monkeypatch):
    from app.modules.assistant import skill_registry as module

    (tmp_path / "leaky.md").write_text(
        "---\nname: leaky\ntitle: T\nsummary: S\n---\n\n"
        "aws.s3.secret_key = 'realsecretvalue'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "_SKILL_DIR", tmp_path)
    with pytest.raises(SkillLibraryError):
        module.SkillLibrary()


async def test_load_skill_tool_returns_the_body():
    tool = LoadSkillTool()
    invocation = ToolInvocation(
        tool_call_id="c1", tool_name="load_skill", arguments={"name": "debug-sql"}
    )
    outcome = await tool.run(invocation, context=None)
    assert outcome.ok
    assert "Skill: debug-sql" in outcome.summary


async def test_load_skill_tool_reports_unknown_name_with_the_catalog():
    tool = LoadSkillTool()
    invocation = ToolInvocation(
        tool_call_id="c1", tool_name="load_skill", arguments={"name": "nope"}
    )
    outcome = await tool.run(invocation, context=None)
    assert not outcome.ok
    assert "create-table" in (outcome.error or "")


async def test_load_skill_tool_rejects_a_missing_name():
    tool = LoadSkillTool()
    invocation = ToolInvocation(tool_call_id="c1", tool_name="load_skill", arguments={})
    outcome = await tool.run(invocation, context=None)
    assert not outcome.ok


def test_load_skill_tool_is_read_only():
    assert LoadSkillTool().classification == "read_only"


def test_load_skill_never_requires_consent_but_query_execute_does():
    from app.modules.assistant.tools import requires_consent
    from app.modules.assistant.tools.query_execute import query_execute_tool

    assert requires_consent(LoadSkillTool()) is False
    assert requires_consent(query_execute_tool) is True
