"""Draft charts in a Studio answer: the reference, its owner and its statement."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import pytest

from app.core.exceptions import NovaException
from app.modules.viz import studio_drafts
from app.modules.viz.contracts.queries import DatasetQueryRequest
from app.modules.viz.studio_drafts import DraftError, DraftLocator
from app.modules.viz.studio_results import fingerprint, normal_sql

SQL = "SELECT region, SUM(revenue) AS revenue FROM sales.orders GROUP BY region"
ALICE = {"username": "alice", "active_role": "ANALYST", "assigned_roles": ["ANALYST"],
         "session_id": "s1", "security_context_version": 1}
STAMP = {"principal": "alice", "active_role": "ANALYST", "security_context_version": 1,
         "session_id": "s1"}


def _chart() -> dict[str, Any]:
    dataset_id = studio_drafts.draft_dataset_id("t1", "c-1")
    return {
        "schema_version": 2, "chart_id": str(uuid4()), "title": "Revenue by region",
        "dataset_id": str(dataset_id), "dataset_revision": 1, "visual": "bar",
        "encoding": {"kind": "cartesian",
                     "dimensions": [{"field_id": "region"}],
                     "measures": [{"field_id": "revenue", "aggregation": "sum"}]},
    }


def _steps(*, source: dict[str, Any] | None = None, kind: str = "component") -> list[dict]:
    tool = {"kind": "tool", "name": "query_execute", "tool_call_id": "q-1",
            "status": "done", "preview": SQL}
    source = source if source is not None else {
        "statement_fingerprint": fingerprint(normal_sql(SQL)), "database": "sales",
        "tool_call_id": "q-1"}
    if kind == "component":
        block = {"kind": "component", "content_id": "run:3", "component": "DatavizChart",
                 "props": {"title": "Revenue by region", "chart": _chart()}, "source": source}
    else:
        block = {"kind": "ui", "content_id": "run:3",
                 "charts": [{"node": 0, "chart": _chart(), "source": source}]}
    return [tool, block]


class Store:
    def __init__(self, *, owner: str = "alice", rows: list[dict] | None = None) -> None:
        self.owner = owner
        self.rows = rows if rows is not None else [
            {"message_id": "m1", "steps": json.dumps(_steps()), "security_context": STAMP}]
        self.asked: list[tuple[str, str]] = []


@pytest.fixture
def store(monkeypatch) -> Store:
    state = Store()

    class Repository:
        def __init__(self, _connection, actor) -> None:
            self.actor = actor

        async def get_thread(self, thread_id):
            return {"thread_id": thread_id, "user_name": state.owner}

        async def message_with_content(self, thread_id, content_id):
            state.asked.append((thread_id, content_id))
            return state.rows

    @asynccontextmanager
    async def transaction(_actor, read_only=False):
        yield type("Scope", (), {"connection": None})()

    monkeypatch.setattr(studio_drafts, "AssistantMetadataRepository", Repository)
    monkeypatch.setattr(studio_drafts, "metadata_transaction", transaction)
    return state


def test_a_reference_names_only_the_callers_own_record():
    ref = studio_drafts.sign(DraftLocator("t1", "run:3"), "alice")
    assert ref.startswith("dr1.") and "SELECT" not in ref and "sales" not in ref
    assert studio_drafts.verify(ref, "alice") == DraftLocator("t1", "run:3")
    node = studio_drafts.sign(DraftLocator("t1", "run:3", 2), "alice")
    assert studio_drafts.verify(node, "alice").node == 2


@pytest.mark.parametrize("mutate", [
    lambda ref: ref[:-2] + ("AA" if not ref.endswith("AA") else "BB"),
    lambda ref: "dr2" + ref[3:],
    lambda ref: ref + ".x",
    lambda ref: "",
])
def test_a_changed_reference_is_refused(mutate):
    ref = studio_drafts.sign(DraftLocator("t1", "run:3"), "alice")
    with pytest.raises(DraftError):
        studio_drafts.verify(mutate(ref), "alice")


def test_a_reference_replayed_by_another_user_is_refused():
    ref = studio_drafts.sign(DraftLocator("t1", "run:3"), "alice")
    with pytest.raises(DraftError) as refused:
        studio_drafts.verify(ref, "bob")
    assert refused.value.status_code == 404


def test_a_forged_payload_with_another_thread_is_refused():
    ref = studio_drafts.sign(DraftLocator("t1", "run:3"), "alice")
    other = studio_drafts.sign(DraftLocator("t2", "run:3"), "alice")
    forged = ".".join([ref.split(".")[0], other.split(".")[1], ref.split(".")[2]])
    with pytest.raises(DraftError):
        studio_drafts.verify(forged, "alice")


async def test_resolve_reads_the_statement_from_the_producing_tool_step(store):
    ref = studio_drafts.sign(DraftLocator("t1", "run:3"), "alice")
    draft = await studio_drafts.resolve(ref, ALICE)
    assert draft.sql == normal_sql(SQL) and draft.database == "sales"
    assert draft.chart.visual == "bar"
    assert store.asked == [("t1", "run:3")]


async def test_resolve_reads_a_chart_inside_an_interactive_answer(store):
    store.rows = [{"message_id": "m1", "steps": _steps(kind="ui"), "security_context": STAMP}]
    draft = await studio_drafts.resolve(
        studio_drafts.sign(DraftLocator("t1", "run:3", 0), "alice"), ALICE)
    assert draft.locator.node == 0 and draft.chart.visual == "bar"
    with pytest.raises(DraftError):
        await studio_drafts.resolve(
            studio_drafts.sign(DraftLocator("t1", "run:3", 1), "alice"), ALICE)


async def test_a_thread_of_another_principal_is_refused_even_with_a_valid_ref(store):
    store.owner = "bob"
    with pytest.raises(DraftError) as refused:
        await studio_drafts.resolve(studio_drafts.sign(DraftLocator("t1", "run:3"), "alice"),
                                    ALICE)
    assert refused.value.status_code == 404


async def test_a_chart_made_under_another_role_is_refused(store):
    switched = {**ALICE, "active_role": "FINANCE", "assigned_roles": ["ANALYST", "FINANCE"],
                "security_context_version": 2}
    with pytest.raises(DraftError) as refused:
        await studio_drafts.resolve(studio_drafts.sign(DraftLocator("t1", "run:3"), "alice"),
                                    switched)
    assert refused.value.status_code == 409


async def test_a_role_switch_back_in_the_same_session_is_still_refused(store):
    # Same role name, but the session's context changed since the chart was made.
    again = {**ALICE, "security_context_version": 3}
    with pytest.raises(DraftError) as refused:
        await studio_drafts.resolve(studio_drafts.sign(DraftLocator("t1", "run:3"), "alice"),
                                    again)
    assert refused.value.status_code == 409


@pytest.mark.parametrize("source", [
    {"statement_fingerprint": "0" * 64, "database": "sales", "tool_call_id": "q-1"},
    {"statement_fingerprint": fingerprint(normal_sql(SQL)), "database": "sales",
     "tool_call_id": "missing"},
    {"statement_fingerprint": fingerprint(normal_sql("DELETE FROM t")), "database": "sales",
     "statement": "DELETE FROM t"},
    {"statement_fingerprint": fingerprint(normal_sql(SQL)), "tool_call_id": "q-1"},
])
async def test_a_statement_that_does_not_match_its_record_is_refused(store, source):
    store.rows = [{"message_id": "m1", "steps": _steps(source=source),
                   "security_context": STAMP}]
    with pytest.raises(DraftError) as refused:
        await studio_drafts.resolve(studio_drafts.sign(DraftLocator("t1", "run:3"), "alice"),
                                    ALICE)
    assert refused.value.status_code == 422


async def test_a_statement_kept_from_an_earlier_turn_is_read_from_the_chart_record(store):
    source = {"statement_fingerprint": fingerprint(normal_sql(SQL)), "database": "sales",
              "statement": SQL}
    store.rows = [{"message_id": "m1", "steps": _steps(source=source)[1:],
                   "security_context": STAMP}]
    draft = await studio_drafts.resolve(
        studio_drafts.sign(DraftLocator("t1", "run:3"), "alice"), ALICE)
    assert draft.sql == normal_sql(SQL)


@pytest.mark.parametrize("rows", [[], [{"steps": [], "security_context": STAMP}] * 2])
async def test_a_missing_or_ambiguous_message_is_refused(store, rows):
    store.rows = rows
    with pytest.raises(DraftError):
        await studio_drafts.resolve(studio_drafts.sign(DraftLocator("t1", "run:3"), "alice"),
                                    ALICE)


async def test_every_query_is_audited_allowed_or_refused(store, monkeypatch):
    from app.modules.viz import dataset_service

    audits: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []

    async def audit(**record):
        audits.append(record)

    async def query_statement(user, *, identity, database, sql, request):
        runs.append({"user": user["username"], "database": database, "sql": sql})
        return {"rows": []}

    monkeypatch.setattr("app.common.audit.write_audit_log", audit)
    monkeypatch.setattr(dataset_service.native_dataset_service, "query_statement",
                        query_statement)
    ref = studio_drafts.sign(DraftLocator("t1", "run:3"), "alice")
    request = DatasetQueryRequest.model_validate({
        "revision": 1, "dimensions": [{"field_id": "region"}],
        "measures": [{"field_id": "revenue", "aggregation": "sum"}], "limit": 100})

    await studio_drafts.query(ref, ALICE, request)
    assert runs == [{"user": "alice", "database": "sales", "sql": normal_sql(SQL)}]
    assert audits[-1]["status"] == "SUCCESS" and audits[-1]["object_name"] == "run:3"
    assert audits[-1]["active_role"] == "ANALYST"

    with pytest.raises(DraftError):
        await studio_drafts.query(ref, {**ALICE, "username": "bob"}, request)
    assert audits[-1]["status"] == "DENIED" and audits[-1]["object_name"] == "invalid_reference"

    stale = request.model_copy(update={"revision": 2})
    with pytest.raises(DraftError) as refused:
        await studio_drafts.query(ref, ALICE, stale)
    assert refused.value.status_code == 409 and audits[-1]["status"] == "DENIED"

    async def denied(*_args, **_kwargs):
        raise NovaException("Access denied", status_code=403)

    monkeypatch.setattr(dataset_service.native_dataset_service, "query_statement", denied)
    with pytest.raises(NovaException):
        await studio_drafts.query(ref, ALICE, request)
    assert audits[-1]["status"] == "DENIED" and len(runs) == 1
    assert all("SELECT" not in json.dumps(item) for item in audits)
