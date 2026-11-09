"""Neva puts SQL into the editor only as a change the person reviews."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.assistant.app_context import NevaAppContext
from app.modules.assistant.tools import ToolInvocation
from app.modules.assistant.tools.client_capability import (
    InvokeClientCapabilityTool,
    _valid_arguments,
)


@pytest.mark.parametrize(("args", "ok"), [
    ({"sql": "SELECT 1", "mode": "replace"}, True),
    ({"sql": "SELECT 1", "mode": "insert"}, True),
    ({"sql": "SELECT 1", "mode": "run"}, False),
    ({"sql": "   ", "mode": "replace"}, False),
    ({"sql": "SELECT 1"}, False),
    ({"sql": "S" * 20_001, "mode": "replace"}, False),
    ({"sql": "SELECT 1", "mode": "replace", "extra": "x"}, False),
])
def test_only_a_bounded_proposal_is_accepted(args, ok):
    assert _valid_arguments("editor.propose_sql", args) is ok


async def test_the_person_reads_plain_words_and_nothing_runs():
    app = NevaAppContext.model_validate({
        "version": 1,
        "surface": {"id": "workspace.sql", "route": "/workspaces"},
        "capabilities": ["editor.propose_sql"],
    })
    tool = InvokeClientCapabilityTool()
    invocation = ToolInvocation("c1", tool.name, {
        "capability": "editor.propose_sql",
        "args": {"sql": "SELECT 1", "mode": "replace"},
    })
    outcome = await tool.run(invocation, SimpleNamespace(app_context=app, user=None))
    assert outcome.ok
    assert outcome.metadata["client_action"]["capability"] == "editor.propose_sql"
    assert "editor.propose_sql" not in outcome.summary and "nothing has run" in outcome.summary
    assert "propose_sql" not in tool.preview(invocation)


async def test_a_page_that_does_not_offer_it_cannot_receive_it():
    app = NevaAppContext.model_validate({
        "version": 1, "surface": {"id": "roles.list", "route": "/roles"},
        "capabilities": ["surface.refresh"],
    })
    invocation = ToolInvocation("c1", "invoke_client_capability", {
        "capability": "editor.propose_sql", "args": {"sql": "SELECT 1", "mode": "replace"},
    })
    outcome = await InvokeClientCapabilityTool().run(
        invocation, SimpleNamespace(app_context=app, user=None)
    )
    assert not outcome.ok


def test_the_open_sql_is_the_reference_when_nothing_is_selected():
    from app.modules.assistant.app_context import resolve_app_references

    app = NevaAppContext.model_validate({
        "version": 1, "surface": {"id": "workspace.sql", "route": "/workspaces"},
        "editor": {"language": "sql", "text": "SELECT city FROM NOVA_RETAIL_FIELD.outlets"},
    })
    assert resolve_app_references("make this faster", app)["editorText"].startswith("SELECT city")
    selected = NevaAppContext.model_validate({
        "version": 1, "surface": {"id": "workspace.sql", "route": "/workspaces"},
        "editor": {"language": "sql", "text": "SELECT 1; SELECT 2", "selectedText": "SELECT 2"},
    })
    references = resolve_app_references("fix this", selected)
    assert references["editorSelection"] == "SELECT 2" and "editorText" not in references


def test_an_editor_holding_a_credential_is_withheld_whole():
    app = NevaAppContext.model_validate({
        "version": 1, "surface": {"id": "workspace.sql", "route": "/workspaces"},
        "editor": {"text": "CREATE USER x IDENTIFIED BY 'hunter22'; SELECT 1"},
    })
    assert app.editor.text == "***"


async def test_a_wrongly_shaped_page_action_is_explained_so_neva_can_retry():
    app = NevaAppContext.model_validate({
        "version": 1, "surface": {"id": "users.list", "route": "/users"},
        "capabilities": ["surface.set_filter"],
    })
    tool = InvokeClientCapabilityTool()
    wrong = await tool.run(ToolInvocation("c1", tool.name, {
        "capability": "surface.set_filter", "args": {"access": "Protected"},
    }), SimpleNamespace(app_context=app, user=None))
    assert not wrong.ok and wrong.recoverable
    assert '"filter"' in wrong.error and '"value"' in wrong.error
    cleared = await tool.run(ToolInvocation("c2", tool.name, {
        "capability": "surface.set_filter", "args": {"filter": "access", "value": ""},
    }), SimpleNamespace(app_context=app, user=None))
    assert cleared.ok


def test_a_page_s_filter_choices_and_counts_reach_neva():
    app = NevaAppContext.model_validate({
        "version": 1, "surface": {"id": "users.list", "route": "/users"},
        "view": {"filters": {"access": ""}, "filterOptions": {"access": ["", "Protected"]},
                 "summary": {"shown": 3, "total": 5}, "ignored": True},
    })
    view = app.prompt_data()["view"]
    assert view["filterOptions"] == {"access": ["", "Protected"]}
    assert view["summary"] == {"shown": 3, "total": 5}
    assert "ignored" not in view


async def test_the_editor_only_receives_sql_that_passed_its_check():
    app = NevaAppContext.model_validate({
        "version": 1, "surface": {"id": "workspace.sql", "route": "/workspaces"},
        "capabilities": ["editor.propose_sql"],
    })
    tool = InvokeClientCapabilityTool()
    invocation = ToolInvocation("c1", tool.name, {
        "capability": "editor.propose_sql", "args": {"sql": "SELECT 1;", "mode": "replace"},
    })
    unchecked = await tool.run(invocation, SimpleNamespace(
        app_context=app, user=None, checked_sql=set()))
    assert not unchecked.ok and unchecked.recoverable
    checked = await tool.run(invocation, SimpleNamespace(
        app_context=app, user=None, checked_sql={"SELECT 1"}))
    assert checked.ok


def test_only_a_bare_question_counts_as_asking_back():
    from app.modules.assistant.service import _only_a_question

    assert _only_a_question("Which date range should it cover?")
    assert not _only_a_question("Revenue was $5M last quarter. Want a breakdown?")
    assert not _only_a_question("Here is the total.\n\nAnything else?")
