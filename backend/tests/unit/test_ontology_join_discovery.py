"""Join discovery from data: profiles, KMV inclusion, the decision rule and its score."""

import random
import re

import pytest

from app.modules.ontology.evidence.joins import candidates, score, select, tokens
from app.modules.ontology.evidence.profiling import (
    ColumnProfile,
    Sketch,
    containment,
    parse_table_profile,
    quote,
    sketch_sql,
    table_profile_sql,
    type_family,
)


def _h(value) -> int:
    import hashlib

    return int.from_bytes(hashlib.blake2b(str(value).encode(), digest_size=4).digest(), "big")


def sketch(values, size=256):
    hashes = sorted({_h(value) for value in values})
    return Sketch(hashes=frozenset(hashes[:size]), ndv=len(set(values)), size=size)


def test_identifiers_are_quoted_or_refused_never_escaped():
    assert quote("outlet_id") == "`outlet_id`"
    for bad in ("a`b", "x; DROP TABLE t", "", "1abc"):
        with pytest.raises(ValueError):
            quote(bad)
    sql = table_profile_sql("NOVA_RETAIL_FIELD", "sales_orders", ["outlet_id", "quantity"])
    assert sql.startswith("SELECT COUNT(*) AS n, approx_count_distinct(`outlet_id`) AS d0")
    assert "FROM `NOVA_RETAIL_FIELD`.`sales_orders`" in sql
    assert re.search(r"ORDER BY h LIMIT 256$", sketch_sql(ColumnProfile(
        "db", "t", "c", "varchar", 1, 1, 1)))


def test_profiles_parse_counts_and_derive_uniqueness_and_nulls():
    columns = [("outlet_id", "varchar"), ("note", "varchar")]
    profile = parse_table_profile("db", "outlets", columns, [100, 100, 100, 7, 7, 3, 40, 2, 9])
    key, note = profile
    assert key.uniqueness == 1.0 and key.null_rate == 0.0 and key.family == "text"
    assert note.ndv == 3 and note.null_rate == pytest.approx(0.6)
    assert type_family("decimal(18,2)") is None and type_family("BIGINT") == "integer"


def test_kmv_inclusion_is_exact_for_small_domains_and_close_for_large_ones():
    assert containment(sketch(range(50)), sketch(range(100))) == 1.0
    assert containment(sketch(range(100)), sketch(range(50))) == 0.5
    rng = random.Random(7)
    big = list(range(20000))
    part = rng.sample(big, 5000) + list(range(30000, 30500))  # 5000 of 5500 inside
    estimate = containment(sketch(part), sketch(big))
    # Small-in-large inclusion is noisy (0.77 against 0.91 here); the sketch only has to
    # keep the pair for exact verification, which decides.
    from app.modules.ontology.evidence.joins import CANDIDATE_CONTAINMENT

    assert estimate > CANDIDATE_CONTAINMENT and abs(estimate - 5000 / 5500) < 0.2


def profile(table, column, datatype, rows, ndv, length=(7, 7), database="db"):
    return ColumnProfile(database, table, column, datatype, rows, ndv, rows, *length)


def lab():
    outlets = [f"OUT-{i:03d}" for i in range(100)]
    employees = [f"EMP-{i:03d}" for i in range(24)]
    products = list(range(1, 13))
    profiles = [
        profile("outlets", "outlet_id", "varchar", 100, 100),
        profile("employees", "employee_id", "varchar", 24, 24),
        profile("products", "product_id", "int", 12, 12, (1, 2)),
        profile("sales", "outlet_id", "varchar", 5000, 90),
        profile("sales", "salesman_id", "varchar", 5000, 20),  # a role: no name evidence
        profile("sales", "product_id", "int", 5000, 12, (1, 2)),
        profile("sales", "quantity", "int", 5000, 10, (1, 2)),  # small ints: chance inclusion
        profile("sales", "status", "varchar", 5000, 3, (4, 9)),
    ]
    sketches = {
        "db.outlets.outlet_id": sketch(outlets),
        "db.employees.employee_id": sketch(employees),
        "db.products.product_id": sketch(products),
        "db.sales.outlet_id": sketch(outlets[:90]),
        "db.sales.salesman_id": sketch(employees[:20]),
        "db.sales.product_id": sketch(products),
        "db.sales.quantity": sketch(range(1, 11)),
        "db.sales.status": sketch(["Done", "Cancelled", "Open"]),
    }
    return profiles, sketches


def test_discovery_finds_named_and_role_joins_and_rejects_chance_inclusion():
    found = candidates(*lab())
    chosen = {(item.source, item.target) for item in select(found)}
    assert ("db.sales.outlet_id", "db.outlets.outlet_id") in chosen
    assert ("db.sales.product_id", "db.products.product_id") in chosen
    # A role is carried by data alone: full inclusion of text ids into a key.
    assert ("db.sales.salesman_id", "db.employees.employee_id") in chosen
    # 1..10 lies inside product ids 1..12, but numbers without a name are not a join.
    assert not any(source == "db.sales.quantity" for source, _ in chosen)
    quantity = next(item for item in found if item.source == "db.sales.quantity")
    assert quantity.containment == 1.0 and not quantity.accepted()
    # Every candidate keeps its evidence for review and for a calibrated model.
    assert set(quantity.as_dict()) >= {"containment", "target_uniqueness", "name_evidence"}


def test_each_source_column_keeps_one_target():
    profiles, sketches = lab()
    profiles.append(profile("outlets_copy", "outlet_id", "varchar", 100, 100))
    sketches["db.outlets_copy.outlet_id"] = sketches["db.outlets.outlet_id"]
    chosen = select(candidates(profiles, sketches))
    sources = [item.source for item in chosen]
    assert len(sources) == len(set(sources))


def test_a_column_identifies_its_table_only_by_the_head_noun():
    from app.modules.ontology.evidence.joins import identifies_table

    assert identifies_table("event_id", "fulfillment_events")
    assert identifies_table("outlet_id", "outlets")
    assert not identifies_table("customer_id", "customer_health_events")
    assert not identifies_table("city", "warehouses")


def test_names_split_on_case_and_separators():
    assert tokens("salesmanId") == {"salesman", "id"}
    assert tokens("route_plan_id") == {"route", "plan", "id"}


def test_score_counts_reversed_pairs_and_reports_misses():
    result = score(
        [("a.t.x", "a.u.x"), ("a.v.y", "a.w.y"), ("a.p.k", "a.q.k")],
        [("a.u.x", "a.t.x"), ("a.v.y", "a.w.y"), ("a.m.z", "a.n.z")],
    )
    assert result["precision"]["successes"] == 2 and result["precision"]["n"] == 3
    assert result["recall"]["successes"] == 2 and result["recall"]["n"] == 3
    assert result["f1"] == pytest.approx(0.6667, abs=1e-3)
    assert result["false_positives"] == [("a.p.k", "a.q.k")]
    assert result["missed"] == [("a.m.z", "a.n.z")]


# --- the run ------------------------------------------------------------------------------

TABLES = {
    "outlets": {"outlet_id": [f"OUT-{i:03d}" for i in range(100)]},
    "employees": {"employee_id": [f"EMP-{i:03d}" for i in range(24)]},
    "sales_orders": {
        "outlet_id": [f"OUT-{i % 90:03d}" for i in range(500)],
        "salesman_id": [f"EMP-{i % 20:03d}" for i in range(500)],
        "quantity": [1 + i % 10 for i in range(500)],
    },
}


async def fake_engine(sql, model, _user):
    database = model["database_name"]
    if "information_schema.columns" in sql:
        assert f"table_schema = '{database}'" in sql
        rows = [[table, column, "int" if column == "quantity" else "varchar"]
                for table, columns in TABLES.items() for column in columns]
        return ["table_name", "column_name", "data_type"], rows, None
    if sql.startswith("SELECT COUNT(*), COUNT(t.v)"):
        left = re.search(r"CAST\(`([^`]+)` AS VARCHAR\) AS v FROM `[^`]+`\.`([^`]+)`", sql)
        right = re.findall(r"CAST\(`([^`]+)` AS VARCHAR\) AS v FROM `[^`]+`\.`([^`]+)`", sql)[1]
        source = {str(v) for v in TABLES[left[2]][left[1]]}
        target = {str(v) for v in TABLES[right[1]][right[0]]}
        return ["n", "m"], [[len(source), len(source & target)]], None
    table = re.search(r"FROM `[^`]+`\.`([^`]+)`", sql)[1]
    data = TABLES[table]
    if sql.startswith("SELECT COUNT(`"):
        column = re.search(r"COUNT\(`([^`]+)`\)", sql)[1]
        return ["n", "d"], [[len(data[column]), len(set(data[column]))]], None
    if sql.startswith("SELECT COUNT(*)"):
        row = [len(next(iter(data.values())))]
        for column in re.findall(r"approx_count_distinct\(`([^`]+)`\)", sql):
            values = [str(value) for value in data[column]]
            row += [len(set(values)), len(values), min(map(len, values)),
                    max(map(len, values))]
        return ["n"], [row], None
    column = re.search(r"CAST\(`([^`]+)` AS VARCHAR\)", sql)[1]
    hashes = sorted({_h(value) for value in data[column]})
    return ["h"], [[value] for value in hashes[:256]], None


def join_service():
    from app.modules.ontology.evaluation.service import EvaluationService
    from tests.unit._ontology import FakeRepository

    async def models(_user):
        return {}

    return EvaluationService(FakeRepository(), executor=fake_engine, models=models)


async def test_a_join_run_profiles_as_the_caller_and_scores_against_known_joins(monkeypatch):
    from app.modules.ontology import access
    from app.modules.ontology.evidence.discovery import JoinRunRequest
    from tests.unit._ontology import ADMIN, user

    async def record(**_):
        return "audit"

    monkeypatch.setattr(access, "write_audit_log", record)
    service = join_service()
    gold = [("LAB.sales_orders.outlet_id", "LAB.outlets.outlet_id"),
            ("LAB.sales_orders.salesman_id", "LAB.employees.employee_id")]
    run = await service.start_joins(JoinRunRequest(databases=["LAB"], gold=gold), ADMIN)
    assert run.kind == "joins" and run.items == sorted(f"LAB.{t}" for t in TABLES)
    for _ in range(10):
        run = await service.step(run.id, ADMIN)
        if run.status != "RUNNING":
            break
    assert run.status == "COMPLETED", run.error
    joins = run.summary["joins"]
    assert joins["precision"]["value"] == 1.0 and joins["recall"]["value"] == 1.0
    assert run.summary["columns"] is None  # table lists are not kept after the run
    results = await service.visible_results(run.id, ADMIN)
    proposed = [item for item in results if item.outcome == "proposed"]
    assert {item.detail["source"] for item in proposed} == {
        "LAB.sales_orders.outlet_id", "LAB.sales_orders.salesman_id"}
    assert all(item.detail["declared"] for item in proposed)
    # Profiles reflect what one principal saw in one role: nobody else reads or advances them.
    other = user("ada", "ACCOUNTADMIN")
    with pytest.raises(Exception) as denied:
        await service.visible_results(run.id, other)
    assert getattr(denied.value, "status_code", None) == 404


async def test_the_overview_shows_a_join_run_only_to_the_context_that_ran_it(monkeypatch):
    from app.modules.ontology import access
    from app.modules.ontology.evaluation import metrics
    from app.modules.ontology.evidence.discovery import JoinRunRequest
    from tests.unit._ontology import ADMIN, user

    async def record(**_):
        return "audit"

    async def nothing(*_args, **_kwargs):
        return {"items": []}

    async def no_findings(*_args, **_kwargs):
        return []

    monkeypatch.setattr(access, "write_audit_log", record)
    monkeypatch.setattr("app.modules.ontology.derived.visible_layer", nothing)
    monkeypatch.setattr("app.modules.ontology.lint.lint", no_findings)
    service = join_service()
    run = await service.start_joins(JoinRunRequest(databases=["LAB"], gold=[
        ("LAB.sales_orders.outlet_id", "LAB.outlets.outlet_id")]), ADMIN)
    while run.status == "RUNNING":
        run = await service.step(run.id, ADMIN)
    mine = (await metrics.overview(ADMIN, service))["measured"]["joins"]
    assert mine["run_id"] == run.id and mine["profiled_columns"] == 5
    assert mine["joins"]["recall"]["value"] == 1.0
    other = await metrics.overview(user("ada", "ACCOUNTADMIN"), service)
    assert other["measured"]["joins"] is None


async def test_reviewers_confirm_an_undeclared_join_and_the_score_follows(monkeypatch):
    from app.modules.ontology import access
    from app.modules.ontology.evaluation.service import ReviewBatch, ReviewItem
    from app.modules.ontology.evidence.discovery import JoinRunRequest
    from tests.unit._ontology import ADMIN

    async def record(**_):
        return "audit"

    monkeypatch.setattr(access, "write_audit_log", record)
    service = join_service()
    run = await service.start_joins(JoinRunRequest(databases=["LAB"], gold=[
        ("LAB.sales_orders.outlet_id", "LAB.outlets.outlet_id")]), ADMIN)
    while run.status == "RUNNING":
        run = await service.step(run.id, ADMIN)
    assert run.summary["joins"]["precision"]["value"] == 0.5
    role = "join:LAB.sales_orders.salesman_id->LAB.employees.employee_id"
    await service.review_results(run.id, ReviewBatch(
        reviewer="claude", kind="external",
        items=[ReviewItem(item=role, verdict="yes", reason="salesman is an employee")]), ADMIN)
    reviewed = (await service.runs())[0].summary
    assert reviewed["undeclared_reviewed"] == 1
    assert reviewed["joins_reviewed"]["precision"]["value"] == 1.0


# --- calibration and automatic decisions ----------------------------------------------------

def labelled(n_per_class, seed=3):
    """Candidates in three databases; each evidence class is right at its own rate."""
    rng = random.Random(seed)
    rates = {(True, True, True): 0.99, (True, False, False): 0.9, (False, False, True): 0.3,
             (False, False, False): 0.0}
    details = []
    for index, ((chosen, names, same), rate) in enumerate(sorted(rates.items()) * n_per_class):
        right = rng.random() < rate
        details.append({
            "source": f"db{index % 3}.t{index}.c", "target": f"db{index % 3}.k{index}.id",
            "proposed": chosen, "names_target_table": names, "same_name": same,
            "declared": right, "declared_direction": right,
        })
    return details


def test_labels_trust_declared_directions_and_reviews_and_leave_open_proposals_unknown():
    from app.modules.ontology.evidence.calibration import label

    assert label({"declared": True, "declared_direction": True}, None) == 1
    # The reverse of a declared join is not a join in that direction.
    assert label({"declared": True, "declared_direction": False}, None) == 0
    assert label({"proposed": True}, "no") == 0 and label({"proposed": True}, "yes") == 1
    assert label({"proposed": True}, None) is None
    assert label({}, None) == 0


def test_probabilities_are_class_precision_measured_on_unseen_databases():
    from app.modules.ontology.evidence.calibration import calibrate

    details = labelled(120)  # a class needs ~50 wrong joins before rejection is automatic
    out, summary = calibrate(details, [None] * len(details))
    measured = summary["calibration"]
    # An unreviewed proposal outside the declared joins has no label yet.
    unknown = sum(1 for item in details if item["proposed"] and not item["declared"])
    assert unknown and measured["n"] == len(details) - unknown
    assert measured["groups"] == ["db0", "db1", "db2"]
    assert measured["auc"] > 0.9 and measured["ece"] < 0.05
    strong = next(item for item in out if item["proposed"] and item["names_target_table"])
    assert strong["probability"] > 0.97 and strong["decision"] == "accept"
    weak = next(item for item in out if not item["proposed"] and not item["same_name"])
    assert weak["probability"] < 0.01 and weak["decision"] == "reject"
    middle = next(item for item in out if item["same_name"] and not item["proposed"])
    assert middle["decision"] == "review"
    assert middle["explanation"]["labelled"] > 0
    decisions = summary["decisions"]
    assert decisions["accepted_precision"]["value"] >= 0.97
    assert decisions["rejected_misses"]["value"] <= 0.01
    assert summary["thresholds"] == {"accept": 0.97, "reject": 0.01}


def test_no_probability_is_reported_without_enough_labelled_joins():
    from app.modules.ontology.evidence.calibration import calibrate

    details = labelled(2)
    for item in details:
        item["declared"] = item["declared_direction"] = False
    _out, summary = calibrate(details, [None] * len(details))
    assert summary == {"calibration": None, "decisions": None}


def test_declared_joins_come_from_the_views_many_to_one_relationships():
    from app.modules.ontology.evidence.discovery import declared_joins
    from tests.benchmark.retail_field.views import VIEWS

    models = {
        name: {"definition": definition, "database_name": "NOVA_RETAIL_FIELD"}
        for name, definition in VIEWS.items()
    }
    found = declared_joins(models)
    assert ("NOVA_RETAIL_FIELD.sales_orders.outlet_id",
            "NOVA_RETAIL_FIELD.outlets.outlet_id") in found
    # Every pair points from the many side at a column of another table.
    assert all(source.split(".")[1] != target.split(".")[1] for source, target in found)
    assert all(len(side.split(".")) == 3 for pair in found for side in pair)
