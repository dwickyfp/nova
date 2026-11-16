"""Release QA NDV-CHART-016: server-side stable sort and pagination for tables.

A table over a large result reads one bounded page at a time. Pages follow the
planner's total order (explicit sort keys, then every dimension), so walking
``next_offset`` returns each row exactly once. The page uses the same governed
query path as any chart, including the stale-source refusal.
"""

import re
import sqlite3
from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.core.exceptions import NovaException
from app.modules.query.repository import QueryResult
from app.modules.viz.contracts.queries import MAX_QUERY_OFFSET, DatasetQueryRequest
from tests.unit.test_native_viz_datasets import (  # noqa: F401 - pytest fixtures
    inspection_request,
    schema_result,
    setup,
    source,
)
from tests.unit.test_native_viz_qa_dataset_query import query_setup  # noqa: F401

# 47 distinct ids; id k appears (k % 3) + 1 times, so row counts tie heavily.
IDS = range(1, 48)
INNER = re.compile(r"FROM \(.*?\n\) AS `nova_dataset`", re.DOTALL)


@pytest.fixture
def engine(query_setup):  # noqa: F811
    """Run each planned aggregate in SQLite over a fixed orders table."""
    case = query_setup
    database = sqlite3.connect(":memory:")
    database.execute("CREATE TABLE orders (id INTEGER NOT NULL, amount NUMERIC, created_at TEXT)")
    database.executemany("INSERT INTO orders VALUES (?, 1, NULL)",
                         [(k,) for k in IDS for _ in range((k % 3) + 1)])
    statements: list[str] = []
    original = case.execute.side_effect

    async def execute(**kwargs):
        if "row_sink" not in kwargs:
            return await original(**kwargs)
        statements.append(kwargs["sql"])
        sql, count = INNER.subn("FROM orders AS `nova_dataset`", kwargs["sql"])
        assert count == 1
        cursor = database.execute(sql)
        names = [item[0] for item in cursor.description]
        rows = [list(row) for row in cursor.fetchall()]
        assert len(rows) <= kwargs["max_rows"]
        sink = kwargs["row_sink"]
        await sink.begin(names, ())
        await sink.rows(rows)
        return QueryResult(columns=names, row_count=len(rows), streamed=True)

    case.execute.side_effect = execute
    fields = {f.source_column: f.field_id for f in case.document.fields}
    request = DatasetQueryRequest(
        revision=1, dimensions=[{"field_id": fields["id"]}],
        measures=[{"aggregation": "count"}], sort=[{"column": "m0", "direction": "desc"}],
        limit=10)
    yield case, request, statements
    database.close()


def _expected() -> list[tuple[int, int]]:
    return sorted(((k, (k % 3) + 1) for k in IDS), key=lambda row: (-row[1], row[0]))


def _values(result) -> list[tuple[int, int]]:
    return [(int(row[0].value), int(row[1].value)) for row in result.rows]


async def test_walking_pages_returns_every_row_once_in_a_stable_order(engine):
    case, request, statements = engine
    seen: list[tuple[int, int]] = []
    offsets: list[int] = []
    offset: int | None = 0
    while offset is not None:
        page = await case.service.query(case.user, case.document.dataset_id,
                                        request.model_copy(update={"offset": offset}))
        assert page.offset == offset and len(page.rows) <= request.limit
        assert page.truncated == (page.next_offset is not None)
        offsets.append(offset)
        seen += _values(page)
        offset = page.next_offset
    assert offsets == [0, 10, 20, 30, 40]
    # Disjoint and complete: the pages concatenate to the full ordered result.
    assert seen == _expected()
    assert len(set(seen)) == len(IDS)
    # Ties on the sort key are broken by the dimension, so the order is total.
    assert all("ORDER BY `m0` DESC,`d0` ASC" in sql for sql in statements)
    assert statements[0].endswith("LIMIT 11")
    assert statements[1].endswith("LIMIT 11 OFFSET 10")


async def test_reading_the_same_page_twice_gives_the_same_rows(engine):
    case, request, _ = engine
    paged = request.model_copy(update={"offset": 20})
    first = await case.service.query(case.user, case.document.dataset_id, paged)
    second = await case.service.query(case.user, case.document.dataset_id, paged)
    assert _values(first) == _values(second) == _expected()[20:30]


async def test_an_offset_past_the_last_row_is_an_empty_final_page(engine):
    case, request, _ = engine
    page = await case.service.query(case.user, case.document.dataset_id,
                                    request.model_copy(update={"offset": 100}))
    assert page.rows == () and not page.truncated and page.next_offset is None


async def test_no_next_page_is_offered_beyond_the_offset_cap(engine):
    case, request, _ = engine
    near_cap = request.model_copy(update={"offset": MAX_QUERY_OFFSET, "limit": 10})
    # The engine has fewer rows than the cap; an empty page has no successor.
    page = await case.service.query(case.user, case.document.dataset_id, near_cap)
    assert page.next_offset is None


async def test_more_rows_beyond_the_cap_report_truncation_without_a_next_offset(query_setup):  # noqa: F811
    case = query_setup
    # The default fixture engine returns two rows for a one-row page.
    page = await case.service.query(case.user, case.document.dataset_id, case.request.model_copy(
        update={"offset": MAX_QUERY_OFFSET, "limit": 1}))
    assert page.truncated and len(page.rows) == 1 and page.next_offset is None
    assert case.aggregates[0]["sql"].endswith(f"LIMIT 2 OFFSET {MAX_QUERY_OFFSET}")
    assert case.aggregates[0]["max_rows"] == 2


@pytest.mark.parametrize("offset", [-1, MAX_QUERY_OFFSET + 1, 10**12, "10", 1.0, True, None])
def test_out_of_range_or_untyped_offsets_are_refused(offset):
    with pytest.raises(ValidationError):
        DatasetQueryRequest.model_validate({
            "revision": 1, "measures": [{"aggregation": "count"}], "offset": offset})


def test_unknown_paging_fields_are_still_refused():
    with pytest.raises(ValidationError):
        DatasetQueryRequest.model_validate({
            "revision": 1, "measures": [{"aggregation": "count"}], "cursor": "abc"})


@pytest.mark.parametrize("change", ["schema_version", "physical_id"])
async def test_a_page_of_a_changed_source_is_stale_and_never_runs(
    query_setup, source, change,  # noqa: F811
):
    case = query_setup
    case.provider.get_source_schema.return_value = replace(source, **{change: "b" * 64})
    with pytest.raises(NovaException, match="SCHEMA_STALE") as error:
        await case.service.query(case.user, case.document.dataset_id,
                                 case.request.model_copy(update={"offset": 20}))
    assert error.value.status_code == 409
    assert case.aggregates == []


async def test_a_page_whose_source_changed_while_running_is_discarded(
    query_setup, source,  # noqa: F811
):
    case = query_setup
    changed = replace(source, schema_version="c" * 64)
    case.provider.get_source_schema.side_effect = [source, source, changed, changed]
    with pytest.raises(NovaException, match="SCHEMA_STALE"):
        await case.service.query(case.user, case.document.dataset_id,
                                 case.request.model_copy(update={"offset": 20}))
    assert len(case.aggregates) == 1
    # The page ran under the caller's own session and active role.
    assert case.aggregates[0]["username"] == "alice" and case.aggregates[0]["role"] == "ANALYST"


async def test_a_page_for_another_revision_never_reaches_the_engine(query_setup):  # noqa: F811
    case = query_setup
    with pytest.raises(NovaException) as error:
        await case.service.query(case.user, case.document.dataset_id,
                                 case.request.model_copy(update={"revision": 2, "offset": 10}))
    assert error.value.status_code == 404
    case.execute.assert_not_awaited()
