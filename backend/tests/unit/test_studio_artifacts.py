"""Chart artifacts in a Studio answer: edits before saving, the save suggestion, saving.

The draft's record and the native services are faked at their boundaries; the
real-PG17 revision race lives in
``tests/integration/test_metadata_assistant_policy_bi.py``.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from app.core.exceptions import NovaException
from app.core.metadata_db import MetadataConflict
from app.modules.viz import dataset_service, studio_artifacts, studio_drafts
from app.modules.viz.canvas import legacy_widgets
from app.modules.viz.contracts.charts import ChartDefinition
from app.modules.viz.contracts.dashboards_v2 import ChartWidget
from app.modules.viz.studio_drafts import DraftError, DraftLocator, ResolvedDraft
from app.modules.viz.studio_publish import PublishError, Target
from tests.unit.test_studio_publish import EXISTING, FakeDatasets, docs  # noqa: F401

ALICE = {"username": "alice", "active_role": "ANALYST", "assigned_roles": ["ANALYST"],
         "session_id": "s1", "security_context_version": 1}
SQL = "SELECT brand, SUM(units) AS units FROM sales.orders GROUP BY brand"
REF = "dr1.payload.mac"
DRAFT_DATASET = studio_drafts.draft_dataset_id("t1", "c-1")
CHART_ID = UUID("99999999-9999-4999-8999-999999999999")


def _chart(**overrides: Any) -> dict[str, Any]:
    return {
        "schema_version": 2, "chart_id": str(CHART_ID), "title": "Units by brand",
        "dataset_id": str(DRAFT_DATASET), "dataset_revision": 1, "visual": "bar",
        "encoding": {"kind": "cartesian", "dimensions": [{"field_id": "brand"}],
                     "measures": [{"field_id": "units", "aggregation": "sum"}]},
        **overrides,
    }


def _journal(*extra: dict[str, Any]) -> dict[str, Any]:
    events = [
        {"event": "run.started", "data": {"protocol_version": 2, "role": "ANALYST",
                                          "run_id": "run", "seq": 0}},
        {"event": "artifact.draft", "data": {"part_id": "run#5", "run_id": "run", "seq": 7,
                                             "draft": {"draft_ref": REF, "kind": "dataviz_chart",
                                                       "title": "Units by brand", "revision": 1,
                                                       "chart": _chart(), "content_id": "run:1"}}},
        {"event": "save.suggested", "data": {"part_id": "run#6", "run_id": "run", "seq": 8,
                                             "draft_ref": REF, "draft_refs": [REF],
                                             "capability": "can_save", "reason_code": "OK",
                                             "message": "Save it"}},
        *extra,
    ]
    return {"kind": "event_journal", "protocol_version": 2, "events": events}


class Log:
    """The message's artifact log, with the revision CAS of the real store."""

    def __init__(self) -> None:
        self.revision = 0
        self.entries: list[dict[str, Any]] = []
        self.writes = 0
        self.race: list[dict[str, Any]] | None = None

    async def read(self, user, thread_id, message_id):
        return self.revision, json.loads(json.dumps(self.entries))

    async def write(self, user, thread_id, message_id, revision, entries):
        if self.race is not None:
            # Another tab wrote first: this write loses the CAS.
            self.entries, self.race = self.race, None
            self.revision += 1
            raise MetadataConflict()
        if revision != self.revision:
            raise MetadataConflict()
        self.entries = json.loads(json.dumps(entries))
        self.revision += 1
        self.writes += 1


@pytest.fixture
def draft(monkeypatch) -> Log:
    log = Log()
    resolved = ResolvedDraft(
        locator=DraftLocator("t1", "run:1"), chart=ChartDefinition.model_validate(_chart()),
        sql=SQL, database="sales", message_id="m1", steps=(_journal(),),
    )

    async def resolve(ref, user):
        if ref != REF:
            raise DraftError("This chart is not in your conversations.")
        return resolved

    async def no_audit(**_kwargs):
        return None

    monkeypatch.setattr(studio_drafts, "resolve", resolve)
    monkeypatch.setattr(studio_artifacts, "_read_log", log.read)
    monkeypatch.setattr(studio_artifacts, "_write_log", log.write)
    monkeypatch.setattr("app.common.audit.write_audit_log", no_audit)
    return log


@pytest.fixture
def engine(monkeypatch, docs):  # noqa: F811
    """The caller's zero-row inspection: the docs fixture's fake, or a refusal."""
    datasets: FakeDatasets = docs.datasets
    state: dict[str, int | None] = {"refuse": None}
    original = datasets.inspect

    async def inspect(user, request):
        if state["refuse"] is not None:
            raise NovaException("Source is unavailable or not permitted",
                                status_code=state["refuse"])
        return await original(user, request)

    monkeypatch.setattr(dataset_service.native_dataset_service, "inspect", inspect)
    monkeypatch.setattr(datasets, "inspect", inspect)
    return state


# ── Reading the journal and the log ──────────────────────────────────────


def test_later_events_are_numbered_after_the_turns_terminal_event():
    journal = _journal()["events"]
    log = [{"event": "artifact.draft", "data": {"part_id": "run#5", "draft": {}}},
           {"event": "artifact.saved", "data": {"part_id": "run#5", "draft_ref": REF}}]
    numbered = studio_artifacts.numbered(journal, log)
    # The stored journal ends at 8; the live run.finished took 9.
    assert [item["data"]["seq"] for item in numbered] == [10, 11]
    assert {item["data"]["run_id"] for item in numbered} == {"run"}
    steps = studio_artifacts.with_log([{"kind": "tool"}, _journal()], log)
    assert [e["event"] for e in steps[1]["events"]][-2:] == ["artifact.draft", "artifact.saved"]
    assert steps[0] == {"kind": "tool"}


def test_a_drafts_state_follows_its_revisions_and_its_save():
    journal = _journal()["events"]
    state = studio_artifacts.draft_state(journal, [], REF)
    assert state is not None
    assert (state.part_id, state.suggestion_part_id, state.revision, state.saved) == (
        "run#5", "run#6", 1, None)
    log = [
        {"event": "artifact.draft", "data": {"part_id": "run#5", "draft": {
            "draft_ref": REF, "revision": 2, "chart": _chart(title="Edited")}}},
        {"event": "artifact.saved", "data": {"part_id": "run#5", "draft_ref": REF,
                                             "saved": {"dashboard_id": "d"}}},
    ]
    state = studio_artifacts.draft_state(journal, log, REF)
    assert state is not None and state.revision == 2
    assert state.draft["chart"]["title"] == "Edited" and state.saved == {"dashboard_id": "d"}
    assert studio_artifacts.draft_state(journal, log, "dr1.other.mac") is None


def test_saving_moves_identities_and_keeps_the_definition():
    chart = ChartDefinition.model_validate(_chart(
        filters={"expression": {
            "type": "predicate", "op": "in", "values": [{"type": "string", "value": "Acme"}],
            "field": {"dataset_id": str(DRAFT_DATASET), "dataset_revision": 1,
                      "field_id": "brand"}}},
        sort=[{"field_id": "units", "aggregation": "sum", "direction": "desc"}],
    ))
    saved_dataset = uuid4()
    rebound = studio_artifacts.rebind(
        chart, {"brand": "f-brand", "units": "f-units"}, chart_id=uuid4(),
        dataset_id=saved_dataset, revision=3,
    )
    moved = rebound.model_dump(mode="json")
    original = chart.model_dump(mode="json")
    assert moved["dataset_id"] == str(saved_dataset) and moved["dataset_revision"] == 3
    assert moved["encoding"]["dimensions"][0]["field_id"] == "f-brand"
    assert moved["filters"]["expression"]["field"]["dataset_id"] == str(saved_dataset)
    assert moved["filters"]["expression"]["field"]["dataset_revision"] == 3
    assert moved["filters"]["expression"]["field"]["field_id"] == "f-brand"
    # Nothing else changes: same visual, title, style, sort direction, limits.
    for key in ("visual", "title", "style", "row_limit", "result_overflow", "top_n"):
        assert moved[key] == original[key]
    assert moved["sort"][0]["direction"] == "desc"


# ── Editing before saving ────────────────────────────────────────────────


async def test_an_edit_is_the_next_revision_with_no_rows_or_sql(draft, engine):
    edited = _chart(title="Units per brand", visual="line")
    result = await studio_artifacts.revise(REF, ALICE, chart=edited, base_revision=1)
    assert result["revision"] == 2
    event = result["event"]
    assert event["event"] == "artifact.draft" and event["data"]["part_id"] == "run#5"
    assert event["data"]["seq"] == 10 and event["data"]["run_id"] == "run"
    assert event["data"]["draft"]["chart"]["visual"] == "line"
    stored = json.dumps(draft.entries)
    assert "SELECT" not in stored and "rows" not in stored and SQL not in stored
    assert set(draft.entries[0]["data"]["draft"]) == {
        "draft_ref", "kind", "title", "chart", "revision", "content_id"}


async def test_an_edit_from_an_old_revision_conflicts(draft, engine):
    await studio_artifacts.revise(REF, ALICE, chart=_chart(title="A"), base_revision=1)
    with pytest.raises(DraftError) as raised:
        await studio_artifacts.revise(REF, ALICE, chart=_chart(title="B"), base_revision=1)
    assert raised.value.status_code == 409
    assert draft.writes == 1


async def test_two_tabs_editing_the_same_revision_keep_only_one(draft, engine):
    other = {"event": "artifact.draft", "data": {"part_id": "run#5", "draft": {
        "draft_ref": REF, "revision": 2, "chart": _chart(title="Other tab")}}}
    draft.race = [other]
    with pytest.raises(DraftError) as raised:
        await studio_artifacts.revise(REF, ALICE, chart=_chart(title="Mine"), base_revision=1)
    assert raised.value.status_code == 409
    assert [entry["data"]["draft"]["chart"]["title"] for entry in draft.entries] == ["Other tab"]


@pytest.mark.parametrize("change", [
    {"dataset_id": str(uuid4())},
    {"chart_id": str(uuid4())},
    {"encoding": {"kind": "cartesian", "dimensions": [{"field_id": "secret"}],
                  "measures": [{"field_id": "units", "aggregation": "sum"}]}},
])
async def test_an_edit_must_stay_on_its_own_data(draft, engine, change):
    with pytest.raises(DraftError) as raised:
        await studio_artifacts.revise(REF, ALICE, chart=_chart(**change), base_revision=1)
    assert raised.value.status_code == 422
    assert draft.writes == 0


async def test_an_edit_by_a_role_that_cannot_read_the_data_is_refused(draft, engine):
    engine["refuse"] = 403
    with pytest.raises(DraftError) as raised:
        await studio_artifacts.revise(REF, ALICE, chart=_chart(title="X"), base_revision=1)
    assert raised.value.status_code == 403 and "cannot read" in raised.value.message
    assert draft.writes == 0


# ── Capability ───────────────────────────────────────────────────────────


async def test_capability_follows_what_the_active_role_can_read(draft, engine):
    allowed = await studio_artifacts.capability(REF, ALICE)
    assert allowed["capability"] == "can_save" and allowed["saved"] is None
    engine["refuse"] = 403
    refused = await studio_artifacts.capability(REF, ALICE)
    assert refused["capability"] == "role_cannot_author"
    assert refused["reason_code"] == "ROLE_CANNOT_READ_SOURCE"
    assert "ANALYST" in refused["message"]
    engine["refuse"] = 503
    with pytest.raises(NovaException):
        await studio_artifacts.capability(REF, ALICE)


async def test_a_draft_made_under_another_role_cannot_be_saved(monkeypatch, engine):
    async def other_role(ref, user):
        raise DraftError("made under another role", status_code=409)

    monkeypatch.setattr(studio_drafts, "resolve", other_role)
    result = await studio_artifacts.capability(REF, ALICE)
    assert (result["capability"], result["reason_code"]) == ("role_cannot_author", "ROLE_CHANGED")
    with pytest.raises(PublishError) as raised:
        await studio_artifacts.publish_draft(ALICE, ref=REF, idempotency_key=str(uuid4()),
                                             target=Target("new", None, "Sales"))
    assert (raised.value.status, raised.value.code) == (403, "ROLE_CANNOT_AUTHOR")


# ── Saving ───────────────────────────────────────────────────────────────


async def test_saving_creates_the_dataset_chart_and_widget_as_shown(draft, engine, docs):  # noqa: F811
    key = str(uuid4())
    result = await studio_artifacts.publish_draft(
        ALICE, ref=REF, idempotency_key=key, target=Target("new", None, "Brand review"))
    assert result["status"] == "SUCCEEDED" and result["dashboard_title"] == "Brand review"
    chart = docs.docs[UUID(result["chart_id"])][-1]
    original = ChartDefinition.model_validate(_chart())
    assert chart.dataset_id != DRAFT_DATASET and chart.chart_id != CHART_ID
    for key_name in ("visual", "title", "encoding", "style", "sort", "row_limit"):
        assert getattr(chart, key_name) == getattr(original, key_name)
    dashboard = docs.docs[UUID(result["dashboard_id"])][-1]
    widgets = [w for page in dashboard.pages for w in legacy_widgets(page)]
    assert [w.chart_ref.chart_id for w in widgets if isinstance(w, ChartWidget)] == [
        chart.chart_id]
    assert chart.dataset_id in {ref.dataset_id for ref in dashboard.datasets}
    # The answer records the save; the event names where it went, not its data.
    (saved,) = draft.entries
    assert saved["event"] == "artifact.saved" and saved["data"]["suggestion_part_id"] == "run#6"
    assert result["event"]["data"]["saved"]["dashboard_id"] == result["dashboard_id"]
    assert SQL not in json.dumps(draft.entries)


async def test_saving_again_with_the_same_key_returns_the_first_outcome(draft, engine, docs):  # noqa: F811
    key = str(uuid4())
    target = Target("existing", EXISTING, None)
    first = await studio_artifacts.publish_draft(ALICE, ref=REF, idempotency_key=key,
                                                 target=target)
    calls = list(docs.calls)
    second = await studio_artifacts.publish_draft(ALICE, ref=REF, idempotency_key=key,
                                                  target=target)
    assert {k: v for k, v in second.items() if k != "event"} == {
        k: v for k, v in first.items() if k != "event"}
    assert docs.calls == calls, "a replay must not create or place anything again"
    assert len(draft.entries) == 1 and draft.writes == 1


async def test_the_latest_revision_is_what_gets_saved(draft, engine, docs):  # noqa: F811
    await studio_artifacts.revise(REF, ALICE, chart=_chart(title="Edited", visual="line"),
                                  base_revision=1)
    result = await studio_artifacts.publish_draft(
        ALICE, ref=REF, idempotency_key=str(uuid4()), target=Target("new", None, "X"))
    chart = docs.docs[UUID(result["chart_id"])][-1]
    assert (chart.title, chart.visual) == ("Edited", "line")
    assert result["draft_revision"] == 2


async def test_a_role_that_cannot_read_the_data_cannot_save_even_directly(draft, engine, docs):  # noqa: F811
    engine["refuse"] = 403
    with pytest.raises(PublishError) as raised:
        await studio_artifacts.publish_draft(ALICE, ref=REF, idempotency_key=str(uuid4()),
                                             target=Target("new", None, "X"))
    assert (raised.value.status, raised.value.code) == (403, "ROLE_CANNOT_AUTHOR")
    assert docs.calls == [] and draft.entries == []


async def test_a_dashboard_the_caller_cannot_edit_is_refused_plainly(draft, engine, docs):  # noqa: F811
    docs.editable = False
    with pytest.raises(PublishError) as raised:
        await studio_artifacts.publish_draft(ALICE, ref=REF, idempotency_key=str(uuid4()),
                                             target=Target("existing", EXISTING, None))
    assert (raised.value.status, raised.value.code) == (403, "NOT_PERMITTED")
    assert "ANALYST" in raised.value.message
    assert "dataset" not in docs.calls and draft.entries == []


# ── HTTP ─────────────────────────────────────────────────────────────────


@pytest.fixture
def client(draft, engine):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.deps import get_current_user
    from app.modules.viz.studio_publish_router import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/agents/studio/dataviz")
    app.dependency_overrides[get_current_user] = lambda: ALICE
    return TestClient(app)


BASE = "/api/v1/agents/studio/dataviz"


def test_publish_takes_exactly_one_reference(client):
    target = {"mode": "new", "title": "X"}
    both = client.post(f"{BASE}/publish", json={"preview_ref": "pv1.a.b", "draft_ref": REF,
                                                "idempotency_key": str(uuid4()),
                                                "target": target})
    neither = client.post(f"{BASE}/publish", json={"idempotency_key": str(uuid4()),
                                                   "target": target})
    assert both.status_code == neither.status_code == 422


def test_the_draft_routes_edit_and_save_as_the_caller(client, engine):
    state = client.get(f"{BASE}/drafts/{REF}").json()
    assert state["capability"] == "can_save" and state["revision"] == 1
    assert {field["field_id"] for field in state["fields"]} == {"brand", "units"}
    assert "rows" not in state
    revised = client.post(f"{BASE}/drafts/{REF}/revisions",
                          json={"chart": _chart(title="Y"), "base_revision": 1})
    assert revised.status_code == 200 and revised.json()["revision"] == 2
    stale = client.post(f"{BASE}/drafts/{REF}/revisions",
                        json={"chart": _chart(title="Z"), "base_revision": 1})
    assert stale.status_code == 409
    saved = client.post(f"{BASE}/publish", json={"draft_ref": REF,
                                                 "idempotency_key": str(uuid4()),
                                                 "target": {"mode": "new", "title": "Q"}})
    assert saved.status_code == 200, saved.text
    assert saved.json()["event"]["event"] == "artifact.saved"
    engine["refuse"] = 403
    refused = client.post(f"{BASE}/publish", json={"draft_ref": REF,
                                                   "idempotency_key": str(uuid4()),
                                                   "target": {"mode": "new", "title": "Q"}})
    assert refused.status_code == 403
    assert refused.json()["detail"]["code"] == "ROLE_CANNOT_AUTHOR"
    assert client.get(f"{BASE}/drafts/{REF}/capability").json()["capability"] == (
        "role_cannot_author")


# ── What a turn emits ───────────────────────────────────────────────


def _chart_step(ref: str, title: str = "Units by brand") -> dict[str, Any]:
    return {
        "kind": "component", "component": "DatavizChart", "content_id": f"run:{ref}",
        "props": {"draft_ref": ref, "title": title, "chart": {
            "dataset_id": "88888888-8888-4888-8888-888888888888", "title": title}},
        "source": {"database": "NOVA_DEMO", "statement": "SELECT brand, units FROM sales"},
    }


def _sse(frame: str) -> tuple[str, dict[str, Any]]:
    lines = dict(line.split(": ", 1) for line in frame.strip().splitlines() if ": " in line)
    body = json.loads(lines["data"])
    return lines["event"], body.get("data", body)


async def _emit(steps: list[dict[str, Any]], capability: Any) -> tuple[list[tuple[str, dict]], Any]:
    from types import SimpleNamespace

    from app.modules.assistant import service

    context = SimpleNamespace(steps=steps, user={"username": "alice", "active_role": "ANALYST"})
    calls: list[dict[str, Any]] = []

    async def decide(user: dict[str, Any], **kwargs: Any) -> Any:
        calls.append(kwargs)
        if isinstance(capability, Exception):
            raise capability
        return capability

    with patch.object(studio_artifacts, "authoring_capability", decide):
        frames = await service._artifact_frames(context)
    return [_sse(frame) for frame in frames], SimpleNamespace(context=context, calls=calls)


@pytest.mark.asyncio
async def test_a_turn_announces_each_draft_and_suggests_saving_once():
    can = studio_artifacts.AuthoringCapability("can_save", "OK", studio_artifacts.CAN_SAVE_MESSAGE)
    steps = [_chart_step("dr-a"), _chart_step("dr-b", "Revenue")]
    frames, run = await _emit(steps, can)
    assert [name for name, _ in frames] == ["artifact_draft", "artifact_draft", "save_suggested"]
    suggestion = frames[-1][1]
    assert suggestion["draft_refs"] == ["dr-a", "dr-b"] and suggestion["capability"] == "can_save"
    assert suggestion["message"] == studio_artifacts.SUGGESTION_MANY.format(count=2)
    # The capability is decided as the caller, over the recorded statement.
    assert run.calls[0]["sql"] == "SELECT brand, units FROM sales"
    assert run.calls[0]["database"] == "NOVA_DEMO"
    # The draft carries the definition, never rows or the statement.
    assert "SELECT" not in json.dumps([body for _, body in frames[:2]])

    # A later pass of the same turn neither re-announces nor suggests again.
    again, _ = await _emit(run.context.steps, can)
    assert again == []


@pytest.mark.asyncio
async def test_a_role_that_cannot_author_gets_the_reason_instead():
    refusal = studio_artifacts.cannot_read({"active_role": "VIEWER"})
    frames, _ = await _emit([_chart_step("dr-a")], refusal)
    name, body = frames[-1]
    assert name == "save_suggested" and body["capability"] == "role_cannot_author"
    assert body["reason_code"] == "ROLE_CANNOT_READ_SOURCE" and "VIEWER" in body["message"]


@pytest.mark.asyncio
async def test_an_undecided_capability_suggests_nothing():
    frames, run = await _emit([_chart_step("dr-a")], RuntimeError("engine unavailable"))
    assert [name for name, _ in frames] == ["artifact_draft"]
    assert not any(step.get("kind") == "save_suggestion" for step in run.context.steps)


@pytest.mark.asyncio
async def test_a_turn_without_a_draft_chart_emits_nothing():
    other = {"kind": "component", "component": "KpiTiles", "props": {"items": []}}
    frames, run = await _emit([other], RuntimeError("never asked"))
    assert frames == [] and run.calls == []


def test_no_tool_can_save_a_draft():
    """Saving a chat chart is the person's action: no tool reaches the save path."""
    from pathlib import Path

    import app

    root = Path(app.__file__).parent
    saving = ("publish_draft", "place_chart", "target_dashboard", "studio_publish.publish(")
    offenders = [
        f"{path.relative_to(root)}: {name}"
        for folder in ("modules/agents/tools", "modules/assistant")
        for path in (root / folder).rglob("*.py")
        for name in saving
        if name in path.read_text()
    ]
    assert offenders == []
