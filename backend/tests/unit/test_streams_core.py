"""Streams core: the reducer's CRUD matrix (spec §7.2), typed images, stream WHEN."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app.modules.streams import codec
from app.modules.streams.codec import Column
from app.modules.streams.reducer import ChangeEvent, reduce_changes
from app.modules.streams.schemas import StreamError, StreamMode
from app.modules.task_orchestration import stream_when
from app.sql_frontend.parser import parse_statement

A = {"id": 1, "v": "a"}
B = {"id": 1, "v": "b"}
C = {"id": 1, "v": "c"}


def ev(seq, row_id, op, before, after, ordinal=1):
    return ChangeEvent(seq, ordinal, row_id, op, before, after)


def shape(rows):
    return [(r.action, r.is_update, r.values["v"], r.row_id) for r in rows]


# ── reducer: spec §7.2 ───────────────────────────────────────────────────

MATRIX = [
    ("insert", [ev(1, "r1", "INSERT", None, A)],
     [("INSERT", False, "a", "r1")], [("INSERT", False, "a", "r1")]),
    ("update", [ev(1, "r1", "UPDATE", A, B)],
     [("DELETE", True, "a", "r1"), ("INSERT", True, "b", "r1")], []),
    ("delete", [ev(1, "r1", "DELETE", A, None)], [("DELETE", False, "a", "r1")], []),
    ("insert then delete", [ev(1, "r1", "INSERT", None, A), ev(2, "r1", "DELETE", A, None)],
     [], [("INSERT", False, "a", "r1")]),
    ("insert then update", [ev(1, "r1", "INSERT", None, A), ev(2, "r1", "UPDATE", A, B)],
     [("INSERT", False, "b", "r1")], [("INSERT", False, "a", "r1")]),
    ("update twice", [ev(1, "r1", "UPDATE", A, B), ev(2, "r1", "UPDATE", B, C)],
     [("DELETE", True, "a", "r1"), ("INSERT", True, "c", "r1")], []),
    ("update and back", [ev(1, "r1", "UPDATE", A, B), ev(2, "r1", "UPDATE", B, A)], [], []),
    ("delete then new row with the same key",
     [ev(1, "r1", "DELETE", A, None), ev(2, "r2", "INSERT", None, B)],
     [("DELETE", False, "a", "r1"), ("INSERT", False, "b", "r2")],
     [("INSERT", False, "b", "r2")]),
    ("upsert of an existing key", [ev(1, "r1", "UPDATE", A, B)],
     [("DELETE", True, "a", "r1"), ("INSERT", True, "b", "r1")], []),
    ("nothing", [], [], []),
]


@pytest.mark.parametrize(("case", "events", "standard", "append_only"), MATRIX,
                         ids=[m[0] for m in MATRIX])
def test_crud_matrix(case, events, standard, append_only):
    assert shape(reduce_changes(events, StreamMode.STANDARD)) == standard
    assert shape(reduce_changes(events, StreamMode.APPEND_ONLY)) == append_only


def test_events_are_reduced_in_commit_order_whatever_their_input_order():
    events = [ev(2, "r1", "UPDATE", B, C), ev(1, "r1", "UPDATE", A, B)]
    assert shape(reduce_changes(events, StreamMode.STANDARD)) == [
        ("DELETE", True, "a", "r1"), ("INSERT", True, "c", "r1"),
    ]


def test_independent_lineages_in_one_commit():
    events = [ev(1, "r1", "INSERT", None, A, 1), ev(1, "r2", "DELETE", B, None, 2)]
    assert shape(reduce_changes(events, StreamMode.STANDARD)) == [
        ("INSERT", False, "a", "r1"), ("DELETE", False, "b", "r2"),
    ]


def test_null_and_typed_equality():
    with_null = {"id": 1, "v": None}
    assert reduce_changes([ev(1, "r1", "UPDATE", with_null, dict(with_null))],
                          StreamMode.STANDARD) == []
    one = {"id": 1, "v": Decimal("1.50")}
    same = {"id": 1, "v": Decimal("1.5")}
    assert reduce_changes([ev(1, "r1", "UPDATE", one, same)], StreamMode.STANDARD) == []


# ── codec ───────────────────────────────────────────────────────────────

COLUMNS = (
    Column("id", "bigint"), Column("big", "largeint"), Column("name", "varchar(20)"),
    Column("amount", "decimal(10,2)"), Column("flag", "boolean"), Column("day", "date"),
    Column("at", "datetime"), Column("ratio", "double"),
)
ROW = {
    "id": 7, "big": 2**100, "name": "O'Brien \\ x", "amount": Decimal("12.50"), "flag": True,
    "day": dt.date(2026, 11, 11), "at": dt.datetime(2026, 11, 11, 10, 30, 0, 123456),
    "ratio": 0.1,
}


def test_images_round_trip_exactly():
    text = codec.encode_image(COLUMNS, ROW)
    assert codec.decode_image(COLUMNS, text) == ROW
    assert codec.decode_image(COLUMNS, codec.encode_image(COLUMNS, {**ROW, "name": None}))[
        "name"
    ] is None


def test_literals_are_typed_and_quoted():
    assert codec.literal(COLUMNS[2], ROW["name"]) == "CAST('O''Brien \\\\ x' AS varchar(20))"
    assert codec.literal(COLUMNS[3], ROW["amount"]) == "CAST('12.50' AS decimal(10,2))"
    assert codec.literal(COLUMNS[4], False) == "FALSE"
    assert codec.literal(COLUMNS[0], None) == "CAST(NULL AS bigint)"
    assert codec.literal(COLUMNS[1], ROW["big"]) == f"CAST('{2**100}' AS largeint)"


@pytest.mark.parametrize("sql_type", ["json", "array<int>", "varbinary", "bitmap", "map<int,int>"])
def test_inexact_types_are_refused(sql_type):
    assert not codec.supported(sql_type)


def test_a_mismatched_image_needs_review():
    with pytest.raises(StreamError) as caught:
        codec.decode_image(COLUMNS[:2], '{"id": 1}')
    assert caught.value.code == "STREAM_SCHEMA_REVIEW_REQUIRED"


def test_key_hash_depends_only_on_the_key():
    keys = ("id",)
    assert codec.key_hash(COLUMNS, keys, ROW) == codec.key_hash(COLUMNS, keys, {**ROW, "name": "x"})
    assert codec.key_hash(COLUMNS, keys, ROW) != codec.key_hash(COLUMNS, keys, {**ROW, "id": 8})


def test_non_finite_floats_are_refused():
    with pytest.raises(StreamError):
        codec.encode(Column("x", "double"), float("nan"))


# ── stream WHEN ─────────────────────────────────────────────────────────


def when(text: str):
    parsed = parse_statement(f"CREATE TASK t WHEN {text} AS INSERT INTO z SELECT 1")
    node = parsed.statement_context
    for child in (node.getChild(i) for i in range(node.getChildCount())):
        inner = getattr(child, "taskWhenClause", None)
        if inner and inner():
            return inner().expression()
    raise AssertionError("no WHEN")


def test_compiles_the_boolean_subset():
    tree = stream_when.compile_when(
        when("NOT (SYSTEM$STREAM_HAS_DATA('a.s') AND NOVA_STREAM_HAS_DATA('b')) OR "
             "SYSTEM$STREAM_HAS_DATA('c.default.s')"),
        "db",
    )
    assert tree == {"op": "or", "args": [
        {"op": "not", "args": [{"op": "and", "args": [
            {"op": "has_data", "stream": "a.s"}, {"op": "has_data", "stream": "db.b"}]}]},
        {"op": "has_data", "stream": "c.s"},
    ]}
    assert stream_when.streams_in(tree) == ["a.s", "db.b", "c.s"]
    assert stream_when.triggers_on_data(tree)
    assert not stream_when.triggers_on_data(
        stream_when.compile_when(when("NOT SYSTEM$STREAM_HAS_DATA('a.s')"), "db")
    )


@pytest.mark.parametrize("text", [
    "SYSTEM$STREAM_HAS_DATA('a.s') AND x > 1",
    "SYSTEM$STREAM_HAS_DATA(name)",
    "SYSTEM$STREAM_HAS_DATA('a.s', 'b.s')",
    "SYSTEM$STREAM_HAS_DATA('a.b.c.d')",
])
def test_refuses_anything_else(text):
    with pytest.raises(stream_when.WhenError):
        stream_when.compile_when(when(text), "db")


def test_only_stream_calls_change_evaluation():
    assert not stream_when.mentions_streams(when("load_count > 0"))
    assert stream_when.mentions_streams(when("NOT SYSTEM$STREAM_HAS_DATA('a.s')"))


async def test_evaluation_short_circuits_and_errors_are_not_false():
    calls = []

    async def has_data(name):
        calls.append(name)
        if name == "bad.s":
            raise StreamError("STREAM_SOURCE_BLOCKED", "blocked")
        return name == "a.s"

    tree = {"op": "or", "args": [{"op": "has_data", "stream": "a.s"},
                                 {"op": "has_data", "stream": "bad.s"}]}
    assert await stream_when.evaluate(tree, has_data) is True
    assert calls == ["a.s"]
    with pytest.raises(StreamError):
        await stream_when.evaluate({"op": "and", "args": tree["args"]}, has_data)


def test_persisted_form_round_trips():
    tree = {"op": "has_data", "stream": "a.s"}
    raw = stream_when.dump(tree, {"a.s": "sid"})
    assert stream_when.load(raw) == tree
    assert stream_when.dependencies(raw) == {"a.s": "sid"}
    with pytest.raises(stream_when.WhenError):
        stream_when.load('{"version": 2}')
