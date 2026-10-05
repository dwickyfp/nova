"""Native engine statements report what they change, not an empty effect set."""

from dataclasses import asdict

import pytest

from app.sql_frontend.ast.builder import ast_builders
from app.sql_frontend.parser import parse_statement
from app.sql_frontend.planning.planner import SQLPlanner


def _effects(sql: str):
    analysis = SQLPlanner().preflight(ast_builders.build(parse_statement(sql)))
    return {name for name, value in asdict(analysis.effects).items() if value}


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("KILL 5", {"writes_metadata"}),
        ("ADMIN SET FRONTEND CONFIG ('x' = '1')", {"writes_metadata"}),
        ("CANCEL LOAD FROM db", {"writes_metadata"}),
        ("SHOW PLAN ADVISOR", {"reads_data"}),
        ("ALTER PLAN ADVISOR ADD SELECT 1", {"reads_data", "writes_metadata"}),
        ("TRUNCATE PLAN ADVISOR", {"writes_metadata"}),
        ("EXECUTE AS u WITH NO REVERT", {"changes_security"}),
        (
            "RESTORE SNAPSHOT db.s FROM repo",
            {"writes_data", "replaces_data", "changes_schema", "external_io", "writes_metadata"},
        ),
        ("ADMIN SHOW FRONTEND CONFIG", {"reads_data"}),
    ],
)
def test_engine_statement_effects(sql, expected):
    assert _effects(sql) == expected


@pytest.mark.parametrize(
    "sql", ["BEGIN", "COMMIT", "ROLLBACK", "SET time_zone = '+00:00'", "USE db", "SET CATALOG c"]
)
def test_session_statements_have_no_effects(sql):
    assert _effects(sql) == set()
