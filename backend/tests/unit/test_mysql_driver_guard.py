"""The MySQL driver never splices a mapping parameter into SQL (PYSEC-2026-286)."""

import re
from pathlib import Path

import pytest
from asyncmy.connection import Connection
from asyncmy.cursors import Cursor

from app import mysql_driver_guard  # noqa: F401 - installs the guard


def _connection() -> Connection:
    connection = Connection(host="127.0.0.1", user="nobody", password="", db=None)
    connection.server_status = 0  # set by the handshake; never connected here
    return connection


@pytest.mark.parametrize(("query", "args"), [
    ("SELECT %s", ({"x' OR 1=1 -- ": 1},)),
    ("SELECT %s, %s", ("ok", {"k": "v"})),
    ("SELECT %s", ((1, {"k": "v"}),)),
])
async def test_a_mapping_parameter_value_is_refused_before_any_sql_is_built(query, args):
    cursor = Cursor(_connection())
    with pytest.raises(TypeError, match="cannot be a mapping"):
        cursor.mogrify(query, args)


async def test_scalar_parameters_are_still_escaped():
    cursor = Cursor(_connection())
    assert cursor.mogrify("SELECT %s, %s", ("it's", 3)) == "SELECT 'it\\'s', 3"


def test_every_module_that_opens_a_mysql_connection_installs_the_guard_first():
    """A connection copies the conversion table when it opens, so the guard must already be in."""
    opens = re.compile(r"asyncmy\.(connect|create_pool|Connection)\(")
    guarded = re.compile(r"^\s*from app import mysql_driver_guard\b", re.MULTILINE)
    missing = []
    for path in sorted(Path(__file__).resolve().parents[2].joinpath("app").rglob("*.py")):
        text = path.read_text()
        if opens.search(text):
            uses = [match.start() for match in opens.finditer(text)]
            guard = guarded.search(text)
            if guard is None or guard.start() > min(uses):
                missing.append(path.name)
    assert not missing, missing
