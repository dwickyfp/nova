"""One edition over four desks: sales, workforce, finance and engineering."""

from datetime import timedelta

import pytest

from tests.benchmark.news import dataset, domains, warehouse
from tests.unit._newsroom import Desks, press_time, subject

TODAY = dataset.LAST_DAY


@pytest.fixture
async def desks(monkeypatch):
    room = Desks(monkeypatch)
    await room.enable_all()
    await room.press_all(TODAY)
    return room


def by_view(paper):
    return {section["name"]: section["stories"] for section in paper["sections"]}


async def test_one_edition_carries_every_desk_for_a_reader_of_everything(desks):
    paper = await desks.paper("news_manager", TODAY)

    sections = by_view(paper)
    assert list(sections) == [
        "news_operating_expenses",
        "news_retail_sales",
        "news_service_reliability",
        "news_workforce",
    ]
    assert {subject(row) for row in sections["news_retail_sales"]} == {("city", "Bandung")}
    assert ("city", "Bandung") in {subject(row) for row in sections["news_workforce"]}
    assert ("cost_center", "Marketing") in {
        subject(row) for row in sections["news_operating_expenses"]
    }
    assert {("service", "payments-api"), ("team", "Payments")} <= {
        subject(row) for row in sections["news_service_reliability"]
    }
    stories = [row for rows in sections.values() for row in rows]
    assert [row["head"] for row in stories].count(True) == 1
    assert all(row["view_name"] for row in stories)


async def test_the_incident_spike_leads_the_whole_edition(desks):
    paper = await desks.paper("news_manager", TODAY)

    [head] = [row for rows in by_view(paper).values() for row in rows if row["head"]]
    assert head["metric"] == "incident_count"
    assert head["severity"] == "critical"
    assert head["relative_change"] > 1.5


async def test_a_rise_in_a_cost_reads_as_bad_and_a_fall_as_good(desks):
    await desks.press_all(TODAY - timedelta(days=1))
    today = by_view(await desks.paper("news_manager", TODAY))
    yesterday = by_view(await desks.paper("news_manager", TODAY - timedelta(days=1)))

    overtime = next(row for row in today["news_workforce"] if subject(row) == ("city", "Bandung"))
    savings = next(
        row for row in yesterday["news_operating_expenses"] if subject(row) == ("city", "Surabaya")
    )
    revenue = today["news_retail_sales"][0]
    assert (overtime["change"] > 0, overtime["impact"]) == (True, "unfavorable")
    assert (savings["change"] < 0, savings["impact"]) == (True, "favorable")
    assert (revenue["change"] < 0, revenue["impact"]) == (True, "unfavorable")
    assert overtime["unit"] == "hours"


async def test_a_city_reader_gets_their_city_on_every_business_desk_and_no_engineering(desks):
    sections = by_view(await desks.paper("news_bandung", TODAY))

    assert "news_service_reliability" not in sections
    assert set(sections) == {
        "news_operating_expenses", "news_retail_sales", "news_workforce",
    }
    assert {subject(row) for row in sections["news_retail_sales"]} == {("city", "Bandung")}
    assert {subject(row) for row in sections["news_workforce"]} == {("city", "Bandung")}
    # The Marketing rise spans every city, so a one-city reader cannot reproduce it.
    assert sections["news_operating_expenses"] == []
    assert "payments" not in str(sections)


async def test_another_city_sees_none_of_bandung(desks):
    sections = by_view(await desks.paper("news_jakarta", TODAY))

    assert all(
        subject(row) != ("city", "Bandung") for rows in sections.values() for row in rows
    )
    assert "news_service_reliability" not in sections


async def test_every_reader_matches_the_access_oracle_on_every_desk(desks):
    leaks = []
    for name, principal in desks.estate.principals.items():
        if not principal.news_enabled:
            continue
        shown = {
            (section["view_id"], *subject(row))
            for section in (await desks.paper(name, TODAY))["sections"]
            for row in section["stories"]
        }
        allowed = {
            (table.view_id, *subject(row))
            for table in desks.tables.values()
            for row in desks.stories(TODAY)
            if row.semantic.view_id == table.view_id
            and warehouse.may_see(principal, *subject(row), table)
        }
        if shown != allowed:
            leaks.append((name, sorted(shown ^ allowed, key=str)))

    assert leaks == []


@pytest.mark.parametrize("domain", domains.DOMAINS, ids=lambda item: item.key)
async def test_each_desk_reports_what_its_design_requires_and_nothing_else(monkeypatch, domain):
    room = Desks(monkeypatch)
    await room.enable_all()
    table = room.tables[domain.key]
    for offset in domains.OFFSETS:
        day = TODAY - timedelta(days=offset)
        await room.service.run_cycle(
            table.view_id, warehouse.SERVICE.user(), now=press_time(day)
        )
        pressed = {
            (row.metric, *subject(row), "increase" if row.change > 0 else "decrease")
            for row in room.stories(day)
            if row.semantic.view_id == table.view_id
        }
        expected = domains.expectations(domain, offset)
        required = {key for key, verdict in expected.items() if verdict == "required"}
        assert required <= pressed, (domain.key, offset, required - pressed)
        assert pressed <= set(expected), (domain.key, offset, pressed - set(expected))


async def test_reading_side_by_side_costs_each_view_only_its_own_queries(desks):
    before = {
        key: house.queries["news_manager"] for key, house in desks.estate.houses.items()
    }

    await desks.paper("news_manager", TODAY)

    spent = {
        key: house.queries["news_manager"] - before[key]
        for key, house in desks.estate.houses.items()
    }
    assert spent == {
        warehouse.VIEW_ID: 4,
        "news-workforce": 6,
        "news-operating-expenses": 3,
        "news-service-reliability": 6,
    }
