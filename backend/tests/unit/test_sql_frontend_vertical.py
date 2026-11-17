"""The MySQL client's ``\\G`` terminator in worksheet scripts."""

import pytest

from app.sql_frontend.vertical import split_vertical, split_vertical_script


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT 1", [("SELECT 1", False)]),
        ("SHOW PROCESSLIST\\G", [("SHOW PROCESSLIST", True)]),
        ("SELECT now(), now(1)\\G", [("SELECT now(), now(1)", True)]),
        ("SELECT * FROM information_schema.loads \\G", [
            ("SELECT * FROM information_schema.loads", True)
        ]),
        ("SELECT 1\\G SELECT 2", [("SELECT 1", True), ("SELECT 2", False)]),
        ("SELECT 1\\G\nSELECT 2\\G", [("SELECT 1", True), ("SELECT 2", True)]),
        ("SELECT 1\\G -- trailing note", [("SELECT 1", True)]),
        # Inside literals, identifiers, comments and $$ bodies it is text.
        ("SELECT 'a\\Gb'", [("SELECT 'a\\Gb'", False)]),
        ("SELECT 'it\\'s \\G'", [("SELECT 'it\\'s \\G'", False)]),
        ('SELECT "x\\G"', [('SELECT "x\\G"', False)]),
        ("SELECT `c\\G` FROM t", [("SELECT `c\\G` FROM t", False)]),
        ("SELECT 1 /* \\G */", [("SELECT 1 /* \\G */", False)]),
        ("SELECT 1 -- \\G", [("SELECT 1 -- \\G", False)]),
        ("SELECT $$ \\G $$", [("SELECT $$ \\G $$", False)]),
        # Lower-case \g is not the vertical terminator.
        ("SELECT 1\\g", [("SELECT 1\\g", False)]),
    ],
)
def test_split_vertical(sql, expected):
    assert split_vertical(sql) == expected


def test_split_script_keeps_order_and_marks():
    sql, vertical = split_vertical_script(["SELECT 1\\G", "SELECT 2", "SHOW TABLES\\G"])
    assert sql == ["SELECT 1", "SELECT 2", "SHOW TABLES"]
    assert vertical == [True, False, True]
