"""Business Event Hub: signals, incidents, reader projection and replay safety."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.modules.intelligence import business_events
from app.modules.intelligence.business_events import (
    BusinessEventService,
    BusinessIncident,
    BusinessSignal,
    TransitionRequest,
    story_population,
    windows_overlap,
)
from app.modules.intelligence.contracts import SemanticRef, Window
from app.modules.ontology.binding import MetricBinding
from app.modules.ontology.contracts import Actor
from tests.unit._ontology import ANALYST, FINANCE_STEWARD, OUTSIDER, FakeRepository

DAY = datetime(2026, 11, 5, 17, tzinfo=UTC)
WINDOW = Window(start=DAY, end=DAY + timedelta(days=1))
SALES = SemanticRef(view_id="sales", version=3, fingerprint="f" * 64)
SUPPORT = SemanticRef(view_id="support", version=1, fingerprint="e" * 64)
ACTOR = Actor(principal="news_editor", active_role="news_desk", security_context_version=1)

CONCEPTS = {
    ("sales", "net_revenue"): "con_revenue",
    ("sales", "refund_rate"): "con_refunds",
    ("support", "open_tickets"): "con_tickets",
    ("sales", "units"): "con_units",
}
LINKS = {
    frozenset({"con_revenue", "con_refunds"}): "rel_1",
    frozenset({"con_revenue", "con_tickets"}): "rel_2",
}


def story(
    story_id,
    metric,
    *,
    semantic=SALES,
    city="Enterprise",
    window=WINDOW,
    digest="a",
    change=-10.0,
    severity="warning",
    revision=1,
):
    return SimpleNamespace(
        id=story_id,
        revision=revision,
        semantic=semantic,
        metric=metric,
        slice=SimpleNamespace(dimension="segment", value=city) if city else None,
        window=window,
        change=change,
        severity=severity,
        proofs=[SimpleNamespace(digest=digest * 64)],
    )


class Hub:
    def __init__(self, monkeypatch) -> None:
        self.repository = FakeRepository()
        self.readable: dict[str, set[str]] = {}
        self.audits: list[dict] = []
        self.bind_calls = 0

        async def read_story(story_id, user):
            return object() if user["username"] in self.readable.get(story_id, set()) else None

        async def read_news(news_id, user):
            return object() if user["username"] in self.readable.get(news_id, set()) else None

        async def bind(semantic, metric, definition):
            self.bind_calls += 1
            concept = CONCEPTS.get((semantic.view_id, metric))
            if concept is None:
                return MetricBinding(status="ONTOLOGY_UNBOUND", release_id="ont_rel_0001")
            return MetricBinding(
                status="BOUND", release_id="ont_rel_0001", concept_id=concept,
                concept_revision=1, representation_id="rep_" + concept,
            )

        async def link(a, b):
            return LINKS.get(frozenset({a, b}))

        async def definition(semantic):
            return {}

        async def names(concept_ids, user):
            hidden = {"con_tickets"} if user["username"] == ANALYST["username"] else set()
            return {
                item: {"id": item, "qualified_name": f"Finance.{item[4:]}", "label": item}
                for item in concept_ids - hidden
            }

        @asynccontextmanager
        async def lock(key, **_):
            yield SimpleNamespace(renew=lambda: True)

        async def audit(**kwargs):
            self.audits.append(kwargs)

        monkeypatch.setattr(business_events, "metadata_lock", lock)
        monkeypatch.setattr(business_events, "write_audit_log", audit)
        self.service = BusinessEventService(
            self.repository, read_story=read_story, read_news=read_news, bind=bind,
            link=link, definition=definition, concept_names=names,
        )

    def share(self, source_id, *users):
        self.readable.setdefault(source_id, set()).update(user["username"] for user in users)

    async def record(self, *stories):
        return await self.service.record_stories(list(stories), ACTOR)

    async def incidents(self):
        return await self.repository.current("incidents", BusinessIncident)

    async def signals(self):
        return await self.repository.current("signals", BusinessSignal)


@pytest.fixture
def hub(monkeypatch):
    return Hub(monkeypatch)


# --- identity and replay ---------------------------------------------------------------


async def test_replaying_a_story_ten_times_keeps_one_signal_and_one_incident(hub):
    revenue = story("story_rev", "net_revenue")
    for _ in range(10):
        await hub.record(revenue)
    signals, incidents = await hub.signals(), await hub.incidents()
    assert len(signals) == 1 and len(incidents) == 1
    assert incidents[0].signal_ids == [signals[0].id]
    assert signals[0].incident_id == incidents[0].id and signals[0].status == "CORRELATED"
    assert hub.bind_calls == 1
    # A narrative-only revision keeps the same proofs, so it is the same observation.
    await hub.record(story("story_rev", "net_revenue", revision=2))
    assert len(await hub.signals()) == 1


async def test_a_signal_records_routing_metadata_only(hub):
    (signal,) = await hub.record(story("story_rev", "net_revenue"))
    payload = signal.model_dump_json()
    assert "Enterprise" not in payload and "segment" not in payload
    assert signal.concept.concept_id == "con_revenue" and signal.binding == "BOUND"
    assert signal.direction == "decrease"
    assert signal.population_fingerprint == story_population(
        SimpleNamespace(dimension="segment", value="Enterprise")
    )


async def test_an_interrupted_recording_is_healed_by_the_next_cycle(hub):
    revenue = story("story_rev", "net_revenue")
    hub.repository.fail_after = 1  # the signal is saved, the incident is not
    with pytest.raises(RuntimeError):
        await hub.record(revenue)
    assert len(await hub.signals()) == 1 and await hub.incidents() == []
    hub.repository.fail_after = None
    await hub.record(revenue)
    signals, incidents = await hub.signals(), await hub.incidents()
    assert len(incidents) == 1 and signals[0].incident_id == incidents[0].id

    refunds = story("story_ref", "refund_rate", digest="b")
    hub.repository.fail_after = hub.repository.saves + 2  # incident saved, signal not marked
    with pytest.raises(RuntimeError):
        await hub.record(refunds)
    hub.repository.fail_after = None
    await hub.record(refunds)
    incidents = await hub.incidents()
    assert len(incidents) == 1 and len(incidents[0].signal_ids) == 2
    assert all(item.status == "CORRELATED" for item in await hub.signals())


# --- correlation ---------------------------------------------------------------------------


async def test_aligned_signals_with_approved_links_form_one_incident(hub):
    await hub.record(
        story("story_rev", "net_revenue", severity="warning"),
        story("story_ref", "refund_rate", digest="b", change=5.0),
        story("story_tix", "open_tickets", semantic=SUPPORT, digest="c", severity="critical",
              window=Window(start=DAY + timedelta(hours=6), end=DAY + timedelta(days=1, hours=6))),
    )
    (incident,) = await hub.incidents()
    assert len(incident.signal_ids) == 3
    assert [item.rule for item in incident.grouping] == [
        "opened", "related_concepts", "related_concepts",
    ]
    assert {item.relation_id for item in incident.grouping} == {None, "rel_1", "rel_2"}
    assert incident.severity == "critical"
    assert incident.causal_status == "unknown"
    assert incident.concept_ids == ["con_revenue", "con_refunds", "con_tickets"]


async def test_unrelated_populations_windows_or_concepts_stay_apart(hub):
    await hub.record(
        story("a", "net_revenue"),
        story("b", "refund_rate", city="SMB", digest="b"),
        story("c", "refund_rate", digest="c",
              window=Window(start=DAY + timedelta(days=3), end=DAY + timedelta(days=4))),
        story("d", "units", digest="d"),
    )
    assert len(await hub.incidents()) == 4


async def test_unbound_signals_group_only_with_the_same_metric(hub):
    await hub.record(
        story("a", "gross_margin"),
        story("b", "gross_margin", digest="b"),
        story("c", "discounts", digest="c"),
    )
    incidents = sorted(await hub.incidents(), key=lambda item: len(item.signal_ids))
    assert [len(item.signal_ids) for item in incidents] == [1, 2]
    assert incidents[1].grouping[1].rule == "same_metric"
    assert all(item.binding == "ONTOLOGY_UNBOUND" for item in await hub.signals())


async def test_a_closed_incident_is_never_reopened_by_a_new_signal(hub):
    hub.share("a", FINANCE_STEWARD)
    await hub.record(story("a", "net_revenue"))
    (incident,) = await hub.incidents()
    await hub.service.transition(
        incident.id,
        TransitionRequest(to="RESOLVED", reason="Fixed upstream", expected_revision=1,
                          operation_id="close-0001"),
        FINANCE_STEWARD,
    )
    await hub.record(story("b", "refund_rate", digest="b"))
    assert len(await hub.incidents()) == 2


# --- reader projection ---------------------------------------------------------------------


async def test_readers_see_only_signals_they_can_prove_now(hub):
    await hub.record(
        story("story_rev", "net_revenue"),
        story("story_tix", "open_tickets", semantic=SUPPORT, digest="c", severity="critical"),
    )
    (incident,) = await hub.incidents()
    hub.share("story_rev", ANALYST, FINANCE_STEWARD)
    hub.share("story_tix", FINANCE_STEWARD)

    analyst = await hub.service.get(incident.id, ANALYST)
    assert [item["source"]["id"] for item in analyst["signals"]] == ["story_rev"]
    assert analyst["severity"] == "warning"  # the hidden critical signal is not counted
    assert [item["rule"] for item in analyst["grouping"]] == ["opened"]
    assert "open_tickets" not in str(analyst) and "tickets" not in str(analyst)
    assert "No cause has been established" in analyst["note"]

    steward = await hub.service.get(incident.id, FINANCE_STEWARD)
    assert len(steward["signals"]) == 2 and steward["severity"] == "critical"
    assert steward["signals"][1]["concept"]["qualified_name"] == "Finance.tickets"


async def test_a_reader_who_lost_access_gets_the_same_not_found(hub):
    await hub.record(story("story_rev", "net_revenue"))
    (incident,) = await hub.incidents()
    hub.share("story_rev", ANALYST)
    assert (await hub.service.get(incident.id, ANALYST))["id"] == incident.id
    hub.readable["story_rev"].clear()  # role downgraded after publication
    for incident_id in (incident.id, "inc_missing"):
        with pytest.raises(HTTPException) as hidden:
            await hub.service.get(incident_id, ANALYST)
        assert hidden.value.status_code == 404
        assert hidden.value.detail == business_events.UNAVAILABLE
    assert hub.audits[-1]["status"] == "DENIED"
    assert await hub.service.list(ANALYST) == []


async def test_the_list_shows_open_incidents_a_reader_can_see(hub):
    await hub.record(story("a", "net_revenue"), story("b", "units", digest="b"))
    hub.share("a", ANALYST)
    listed = await hub.service.list(ANALYST)
    assert [item["signals"][0]["source"]["id"] for item in listed] == ["a"]
    assert await hub.service.list(OUTSIDER) == []


# --- lifecycle -------------------------------------------------------------------------------


async def test_incident_transitions_are_checked_idempotent_and_audited(hub):
    await hub.record(story("a", "net_revenue"))
    (incident,) = await hub.incidents()
    hub.share("a", FINANCE_STEWARD)
    body = TransitionRequest(to="INVESTIGATING", reason="Studio mission opened",
                             expected_revision=1, operation_id="move-0001")
    moved = await hub.service.transition(incident.id, body, FINANCE_STEWARD)
    assert moved["status"] == "INVESTIGATING"
    again = await hub.service.transition(incident.id, body, FINANCE_STEWARD)
    assert again["revision"] == moved["revision"]
    assert moved["transitions"][0]["principal"] == "fiona"

    with pytest.raises(HTTPException) as stale:
        await hub.service.transition(
            incident.id,
            TransitionRequest(to="RESOLVED", reason="x", expected_revision=1,
                              operation_id="move-0002"),
            FINANCE_STEWARD,
        )
    assert stale.value.status_code == 409
    with pytest.raises(HTTPException) as illegal:
        await hub.service.transition(
            incident.id,
            TransitionRequest(to="OBSERVING", reason="skip", expected_revision=moved["revision"],
                              operation_id="move-0003"),
            FINANCE_STEWARD,
        )
    assert illegal.value.status_code == 422
    assert hub.audits[-1]["status"] == "DENIED"
    with pytest.raises(HTTPException) as hidden:
        await hub.service.transition(incident.id, body, OUTSIDER)
    assert hidden.value.status_code == 404


# --- monitor detections and helpers ----------------------------------------------------------


async def test_monitor_detections_become_signals_in_the_same_hub(hub):
    news = SimpleNamespace(
        id="news_1", revision=1, semantic=SALES, window=WINDOW, change=-3.0, severity="critical",
        evidence=[SimpleNamespace(digest="d" * 64)],
    )
    monitor = SimpleNamespace(plan={"metrics": ["net_revenue"], "filters": [
        {"field": "segment", "operator": "=", "value": "Enterprise"},
    ]})
    signal = await hub.service.record_news_item(news, monitor, ACTOR)
    assert signal.source.type == "MONITOR_NEWS_ITEM"
    await hub.record(story("story_ref", "refund_rate", digest="b"))
    (incident,) = await hub.incidents()
    assert len(incident.signal_ids) == 2  # the monitor and the story share a population
    assert await hub.service.record_news_item(news, SimpleNamespace(plan={}), ACTOR) is None
    unfiltered = await hub.service.record_news_item(
        news.__class__(**{**vars(news), "id": "news_2"}),
        SimpleNamespace(plan={"metrics": ["net_revenue"], "filters": "not a list"}),
        ACTOR,
    )
    assert unfiltered.population_fingerprint == story_population(None)


def test_population_and_window_rules():
    total = story_population(None)
    assert total != story_population(SimpleNamespace(dimension="segment", value="Enterprise"))
    def window(start_hours, end_hours):
        return Window(
            start=DAY + timedelta(hours=start_hours), end=DAY + timedelta(hours=end_hours)
        )

    day = window(0, 24)
    assert windows_overlap(day, window(12, 48))  # half of the day overlaps
    assert not windows_overlap(day, window(20, 72))  # four hours is less than half
    assert not windows_overlap(day, window(48, 72))


async def test_recording_never_fails_the_news_cycle(monkeypatch, caplog):
    async def boom(*_args, **_kwargs):
        raise RuntimeError("metadata down")

    monkeypatch.setattr(business_events.business_event_service, "record_stories", boom)
    monkeypatch.setattr(business_events.business_event_service, "record_news_item", boom)
    user = {**ANALYST}
    await business_events.record_stories_safely([], user)
    await business_events.record_news_item_safely(None, None, user)
    assert caplog.text.count("not recorded") == 2


async def test_every_newsroom_cycle_records_the_whole_edition(monkeypatch):
    from tests.benchmark.news import dataset
    from tests.unit._newsroom import Newsroom

    recorded: list[tuple[list, dict]] = []

    async def capture(stories, user):
        recorded.append((list(stories), user))

    monkeypatch.setattr(business_events, "record_stories_safely", capture)
    room = Newsroom(monkeypatch)
    await room.enable()
    await room.press(dataset.LAST_DAY)
    await room.press(dataset.LAST_DAY)  # a second cycle records the same edition again
    assert len(recorded) == 2
    first, second = recorded
    assert first[0] and [item.id for item in first[0]] == [item.id for item in second[0]]
    assert first[1]["active_role"]


async def test_metric_bindings_follow_the_published_release(monkeypatch):
    from app.modules.ontology.binding import bind_metric, linked
    from app.modules.ontology.contracts import RelationChange, RepresentationChange
    from tests.unit.test_business_ontology import World
    from tests.unit.test_ontology_grounding import sales_definition

    world = World(monkeypatch)
    await world.domains()
    definition = sales_definition()
    view = world.catalog.publish("sales", definition)
    ref = SemanticRef(view_id="sales", version=view.version, fingerprint=view.fingerprint)
    assert (await bind_metric(ref, "total_revenue", definition, service=world.reads)).status == (
        "NO_RELEASE"
    )
    created = await world.approved(
        [
            world.concept(world.finance, "NetRevenue"),
            RepresentationChange(op="create", concept_id="@0", view_id="sales",
                                 metric="total_revenue"),
            world.concept(world.finance, "Orders"),
            RelationChange(op="create", source_id="@0", type="RELATED_TO", target_id="@2",
                           justification="Revenue comes from orders"),
        ]
    )
    release = await world.release()
    bound = await bind_metric(ref, "total_revenue", definition, service=world.reads)
    assert bound.status == "BOUND" and bound.concept_id == world.concept_id(created, 0)
    assert bound.release_id == release.id
    unbound = await bind_metric(ref, "order_count", definition, service=world.reads)
    assert unbound.status == "ONTOLOGY_UNBOUND"
    changed = sales_definition()
    changed["metrics"][0]["expression"] = "SUM(orders.gross)"
    assert (await bind_metric(ref, "total_revenue", changed, service=world.reads)).status == (
        "ONTOLOGY_UNBOUND"
    )
    revenue, orders = world.concept_id(created, 0), world.concept_id(created, 2)
    assert await linked(orders, revenue, service=world.reads)
    assert await linked(revenue, "con_other", service=world.reads) is None


def test_incident_routes_use_the_news_entitlement(monkeypatch):
    from unittest.mock import AsyncMock

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.common.news_entitlement import _news_user
    from app.modules.intelligence import business_events_router

    service = business_events_router.business_event_service
    monkeypatch.setattr(service, "list", AsyncMock(return_value=[{"id": "inc_1"}]))
    monkeypatch.setattr(service, "get", AsyncMock(return_value={"id": "inc_1"}))
    monkeypatch.setattr(service, "transition", AsyncMock(return_value={"id": "inc_1"}))
    app = FastAPI()
    app.include_router(business_events_router.router, prefix="/intelligence")
    app.dependency_overrides[_news_user] = lambda: ANALYST
    client = TestClient(app)
    assert client.get("/intelligence/incidents?include_closed=true").json() == [{"id": "inc_1"}]
    assert service.list.await_args.kwargs == {"include_closed": True}
    assert client.get("/intelligence/incidents/inc_1").json() == {"id": "inc_1"}
    moved = client.post(
        "/intelligence/incidents/inc_1/transition",
        json={"to": "TRIAGED", "reason": "seen", "expected_revision": 1,
              "operation_id": "route-0001"},
    )
    assert moved.status_code == 200
    assert service.transition.await_args.args[1].to == "TRIAGED"


async def test_default_readers_delegate_to_the_source_owners(monkeypatch):
    from unittest.mock import AsyncMock

    from app.modules.intelligence import engine, newsroom, semantic_views

    monkeypatch.setattr(newsroom.newsroom_service, "readable", AsyncMock(return_value="story"))
    assert await business_events._read_story("s", ANALYST) == "story"
    monkeypatch.setattr(
        engine.intelligence_service, "get", AsyncMock(side_effect=HTTPException(404))
    )
    assert await business_events._read_news("n", ANALYST) is None
    version = AsyncMock(return_value={"fingerprint": "f" * 64, "definition": {"metrics": []}})
    monkeypatch.setattr(semantic_views.semantic_view_service, "_version", version)
    assert await business_events._definition(SALES) == {"metrics": []}
    assert await business_events._definition(SUPPORT) == {}  # fingerprint no longer matches
    assert await business_events._visible_concepts(set(), ANALYST) == {}

    executed = []

    async def execute(sql, params=None):
        executed.append(sql)
        return {"rows": []}

    monkeypatch.setattr(business_events.db, "execute_system", execute)
    await business_events.ensure_event_schema()
    assert len(executed) == len(business_events.EVENT_TABLES)


async def test_visible_concept_names_follow_ontology_visibility(monkeypatch):
    from app.modules.ontology import service as service_module
    from tests.unit.test_business_ontology import World

    world = World(monkeypatch)
    await world.domains()
    created = await world.approved([world.concept(world.finance, "NetRevenue")])
    monkeypatch.setattr(service_module, "ontology_service", world.reads)
    concept_id = world.concept_id(created)
    # Not released yet: named to its steward only.
    assert await business_events._visible_concepts({concept_id}, ANALYST) == {}
    steward = await business_events._visible_concepts({concept_id}, FINANCE_STEWARD)
    assert steward[concept_id]["qualified_name"] == "Finance.NetRevenue"
    await world.release()
    names = await business_events._visible_concepts({concept_id}, ANALYST)
    assert names[concept_id]["qualified_name"] == "Finance.NetRevenue"
    assert await business_events._visible_concepts({concept_id}, OUTSIDER) == {}
