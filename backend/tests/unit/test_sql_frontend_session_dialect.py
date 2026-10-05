"""Nova parses one SQL dialect; a session must not switch the engine to another.

``SET sql_dialect = 'trino'`` makes StarRocks parse later statements with its
Trino parser while Nova keeps classifying them with the pinned StarRocks
grammar, so effects, stage references and governance decoding could describe a
different statement from the one the engine runs. The frontend refuses it.
"""

import pytest

from app.sql_frontend.ast.builder import ast_builders
from app.sql_frontend.context import PlanningContext
from app.sql_frontend.errors import CapabilityUnsupportedError
from app.sql_frontend.parser import parse_statement
from app.sql_frontend.planning.planner import SQLPlanner


def _validate(sql: str) -> None:
    planner = SQLPlanner()
    planner.semantics.validate(ast_builders.build(parse_statement(sql)), PlanningContext())


@pytest.mark.parametrize(
    "sql",
    [
        "SET sql_dialect = 'trino'",
        "SET SESSION sql_dialect = 'Trino'",
        "SET GLOBAL sql_dialect = 'trino'",
        "SET @@session.sql_dialect = 'trino'",
        "SET @@sql_dialect = trino",
        "SET time_zone = '+07:00', sql_dialect = \"trino\"",
        "SET `sql_dialect` = 'trino'",
    ],
)
def test_other_dialects_are_refused(sql):
    with pytest.raises(CapabilityUnsupportedError):
        _validate(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SET sql_dialect = 'StarRocks'",
        "SET sql_dialect = DEFAULT",
        "SET time_zone = '+07:00'",
        "SET sql_mode = 'ONLY_FULL_GROUP_BY'",
        "SET NAMES utf8mb4",
        "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
        "SELECT 'sql_dialect = trino'",
    ],
)
def test_native_session_settings_are_unaffected(sql):
    _validate(sql)
