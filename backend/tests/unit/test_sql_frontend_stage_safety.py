"""Stage references must name what the client wrote, and answers must name stages.

``@stage/*.csv`` makes ``/*`` open a comment. With a later ``*/`` the glob is
swallowed and the reference silently becomes the whole stage, so the frontend
refuses a block comment glued to a stage path. In the other direction, engine
listings and storage errors name physical bucket paths; clients see ``@stage``.
"""

import asyncio
from types import SimpleNamespace

import pytest

from app.sql_frontend.ast.builder import ast_builders
from app.sql_frontend.context import PlanningContext
from app.sql_frontend.errors import SemanticError
from app.sql_frontend.execution.adapters import (
    _logical_location,
    _LogicalLocationSink,
    _stage_location_map,
)
from app.sql_frontend.parser import parse_statement
from app.sql_frontend.planning.planner import SQLPlanner


def _validate(sql: str) -> None:
    planner = SQLPlanner()
    planner.semantics.validate(ast_builders.build(parse_statement(sql)), PlanningContext())


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM @raw/*.csv /* note */ LIMIT 1",
        "SELECT * FROM @raw/2024/x.csv/* note */",
        "SELECT * FROM @raw.sales_*.csv/* note */",
    ],
)
def test_a_comment_glued_to_a_stage_path_is_refused(sql):
    with pytest.raises(SemanticError, match="comment starts directly after a stage path"):
        _validate(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM @raw.*.csv /* note */ LIMIT 1",
        "SELECT * FROM @raw.sales_*.csv",
        "SELECT * FROM @raw/2024/x.csv /* note */",
        "SELECT * FROM @raw /* note */",
        "SELECT 1 /* @raw/*.csv */",
    ],
)
def test_separated_comments_and_dotted_globs_are_accepted(sql):
    _validate(sql)


def _config(bucket: str, prefix: str, endpoint: str | None = None):
    return SimpleNamespace(bucket=bucket, base_prefix=prefix, endpoint=endpoint)


def test_physical_roots_map_to_stage_names_longest_first():
    parsed = SimpleNamespace(
        stage_refs=[
            SimpleNamespace(start=0, stage_name="raw"),
            SimpleNamespace(start=40, stage_name="raw_2024"),
        ]
    )
    location_map = _stage_location_map(
        parsed,
        {
            0: _config("lake", "/tenants/a/"),
            40: _config("lake", "tenants/a/2024", "http://minio.internal:9000/"),
        },
    )

    text = (
        "No files found: 's3://lake/tenants/a/2024/x.csv' or "
        "'s3://lake/tenants/a/y.csv' at http://minio.internal:9000"
    )
    assert _logical_location(text, location_map) == (
        "No files found: '@raw_2024/x.csv' or '@raw/y.csv' at <storage endpoint>"
    )


def test_unresolved_references_contribute_no_mapping():
    parsed = SimpleNamespace(stage_refs=[SimpleNamespace(start=3, stage_name="raw")])
    assert _stage_location_map(parsed, {}) == []


def test_streamed_listing_rows_are_rewritten():
    received = {}

    class Sink:
        async def begin(self, columns, column_types):
            received["columns"] = columns

        async def rows(self, rows):
            received.setdefault("rows", []).extend(rows)

    sink = _LogicalLocationSink(Sink(), [("s3://lake/tenants/a", "@raw")])

    async def run():
        await sink.begin(["PATH", "SIZE"], ("STRING", "BIGINT"))
        await sink.rows([["s3://lake/tenants/a/x.csv", 54], [None, 0]])

    asyncio.run(run())
    assert received == {"columns": ["PATH", "SIZE"], "rows": [["@raw/x.csv", 54], [None, 0]]}
