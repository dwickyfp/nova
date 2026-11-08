"""The offline parts of the join-precision loader and the judge challenge set."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from tests.benchmark.fk_schemas import load
from tests.benchmark.supernova_ontology import judge_challenge


def test_identifiers_nova_refuses_are_rewritten_the_same_way_everywhere():
    assert load.identifier("Order Details") == "Order_Details"
    assert load.identifier("Free Meal Count (K-12)") == "Free_Meal_Count__K_12_"
    assert load.identifier("2nd") == "c_2nd"


def test_the_answer_key_is_every_declared_single_column_foreign_key(tmp_path):
    path = tmp_path / "shop.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE customer (id INTEGER PRIMARY KEY, name TEXT, score REAL);
        CREATE TABLE orders (id INTEGER PRIMARY KEY,
            customer_id INTEGER REFERENCES customer,
            buyer INTEGER REFERENCES customer(id), picture BLOB,
            a INTEGER, b INTEGER, FOREIGN KEY (a, b) REFERENCES pair(x, y));
        CREATE TABLE pair (x INTEGER, y INTEGER, PRIMARY KEY (x, y));
        INSERT INTO customer VALUES (1, 'Ani', 1.5), (2, 'Budi', 2.0);
        INSERT INTO orders VALUES (10, 1, 2, x'00', 1, 1);
    """)
    connection.commit()
    connection.close()
    tables, keys = load.read_schema(path)
    # A key that names no column refers to the parent's primary key; composite keys are out.
    assert sorted(keys) == [("orders.buyer", "customer.id"), ("orders.customer_id", "customer.id")]
    columns = {table.name: dict(table.columns) for table in tables}
    assert columns["customer"] == {"id": "BIGINT", "name": "VARCHAR(65533)", "score": "DOUBLE"}
    assert "picture" not in columns["orders"]  # BLOB columns are left out
    assert not any(table.sampled for table in tables)


def test_a_large_table_is_sampled_only_when_no_key_targets_it(tmp_path, monkeypatch):
    path = tmp_path / "big.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE parent (id INTEGER PRIMARY KEY);
        CREATE TABLE child (id INTEGER, parent_id INTEGER REFERENCES parent(id));
    """)
    connection.executemany("INSERT INTO parent VALUES (?)", [(i,) for i in range(30)])
    connection.executemany("INSERT INTO child VALUES (?, ?)", [(i, i % 30) for i in range(30)])
    connection.commit()
    connection.close()
    monkeypatch.setattr(load, "SAMPLE_ROWS", 10)
    tables, _keys = load.read_schema(path)
    assert {table.name: table.sampled for table in tables} == {"parent": False, "child": True}


def test_a_value_is_written_as_a_safe_literal():
    assert load._literal(None) == "NULL"
    assert load._literal(3) == "3"
    assert load._literal("O'Brien\\") == "'O''Brien\\\\'"


def _case(meaning: str, plan: dict) -> SimpleNamespace:
    return SimpleNamespace(id="evc_1", meaning=meaning, plan=plan, kind="answerable",
                           question="q", status="ACTIVE", reviews=[])


def test_a_top_n_mutant_changes_only_n_and_a_period_mutant_only_the_period():
    case = _case("Units sold, the 5 City [outlets.city] values with the highest result, "
                 "in October 2026", {"metrics": ["units_sold"], "limit": 5,
                                     "time": {"dimension": "d", "range": "2026-10"}})
    assert judge_challenge.mutate(case, None, None, "top_n") == (
        "Units sold, the 3 City [outlets.city] values with the highest result, in October 2026")
    period = judge_challenge.mutate(case, None, ["2026-05-01", "2026-11-04"], "period")
    assert period is not None and "in October 2026" not in period
    assert period.startswith("Units sold, the 5 City")
    # A trend has no single period to move.
    trend = _case("Units sold, per month from May to October 2026",
                  {"metrics": ["units_sold"], "time": {"dimension": "d", "grain": "month",
                                                       "range": "2026-05-01..2026-10-31"}})
    assert judge_challenge.mutate(trend, None, ["2026-05-01", "2026-11-04"], "period") is None


def test_kappa_has_an_interval_and_counts_caught_mutants():
    rows = [("yes", "yes")] * 20 + [("no", "no")] * 19 + [("no", "unsure")]
    scored = judge_challenge.score(rows)
    assert scored["sensitivity_no"] == [19, 20] and scored["specificity"] == [20, 20]
    low, high = scored["interval_95"]
    assert low <= scored["kappa"] <= high <= 1
