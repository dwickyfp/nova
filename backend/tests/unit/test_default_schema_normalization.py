"""Regression tests for ``QueryService._normalize_default_schema_qualification``.

The routine collapses the workspace UI's ``db.default.table`` placeholder to the
StarRocks-compatible ``db.table``. It previously did so with a bare regex over
the whole statement, which corrupted every other ``X.default.Y`` shape in the
text. These are the confirmed false positives from the NOVA-17 audit plus the
cases the routine is *supposed* to rewrite, so the boundary is pinned in both
directions.

Placed at L1: the function is pure over the statement string, so no engine or
fixture is involved (STANDARD SS2 rule 1).
"""

import pytest

from app.common.sql_guard import guard_sql
from app.core.exceptions import ForbiddenSQLError
from app.modules.query.service import QueryService

normalize = QueryService._normalize_default_schema_qualification


# ── Statements that must survive untouched ──────────────────────────────────
#
# Keyed by the *reason* the old implementation got them wrong, because one
# regression test per defect class is the rule (STANDARD SS12 rule 3).

PRESERVED_CASES = [
    # Class 1: a `.default.` segment inside an @stage path. The old `(?<!@)`
    # only guarded the first identifier, so this collapsed to a different file.
    ("@stage1.data.default.csv", "@stage1.data.default.csv"),
    (
        "SELECT * FROM @stage1.data.default.csv",
        "SELECT * FROM @stage1.data.default.csv",
    ),
    (
        "SELECT id FROM @stage1.exports.default.parquet",
        "SELECT id FROM @stage1.exports.default.parquet",
    ),
    # Class 2: a three-part object path that is not a table reference. This is a
    # catalogue/schema/object reference, not the UI placeholder, and the two are
    # the same token shape — only position distinguishes them.
    ("config.default.value", "config.default.value"),
    ("SELECT config.default.value FROM t", "SELECT config.default.value FROM t"),
    # Not preceded by a table-introducing keyword.
    ("SELECT default.value FROM t", "SELECT default.value FROM t"),
    ("SELECT a.default.b FROM t", "SELECT a.default.b FROM t"),
    # An ordinary three-part table reference whose schema is not `default`.
    ("SELECT * FROM mydb.bronze.orders", "SELECT * FROM mydb.bronze.orders"),
    # A two-part reference is already correct and must not be rewritten.
    ("SELECT * FROM db.t", "SELECT * FROM db.t"),
    # Class 3: a `.default.` inside a string literal is user data.
    ("SELECT 'a.default.b' FROM t", "SELECT 'a.default.b' FROM t"),
    ('SELECT "a.default.b" FROM t', 'SELECT "a.default.b" FROM t'),
    ("SELECT 'from a.default.b' AS note", "SELECT 'from a.default.b' AS note"),
    # An escaped quote inside the literal must not end the mask early.
    ("SELECT 'it''s a.default.b' FROM t", "SELECT 'it''s a.default.b' FROM t"),
    # Class 4: a `.default.` inside a comment must not be rewritten either; the
    # comment is echoed back to the user and written to the audit row.
    ("SELECT 1 -- config.default.value", "SELECT 1 -- config.default.value"),
    ("SELECT /* @s.a.default.csv */ 1", "SELECT /* @s.a.default.csv */ 1"),
    ("-- SELECT * FROM db.default.t\nSELECT 1", "-- SELECT * FROM db.default.t\nSELECT 1"),
    # The engine's own variables — `@@` never starts a stage ref, and the old
    # parser treated `@@version` as one elsewhere in the dialect.
    ("SELECT @@version", "SELECT @@version"),
    # No placeholder present at all: the routine must be a no-op.
    ("SELECT 1", "SELECT 1"),
    # A DESCRIBE target that is already engine-shaped, or has no placeholder.
    ("DESCRIBE db.t", "DESCRIBE db.t"),
    ("DESCRIBE t", "DESCRIBE t"),
    ("DESC NOVA_ANALYTICS.channel_performance", "DESC NOVA_ANALYTICS.channel_performance"),
    # A DESCRIBE of a stage reference: the mask wins, as in the FROM case.
    ("DESCRIBE @stage1.data.default.csv", "DESCRIBE @stage1.data.default.csv"),
    # A `.default.` inside a string literal after DESCRIBE is user data.
    ("DESCRIBE 'a.default.b'", "DESCRIBE 'a.default.b'"),
]


@pytest.mark.parametrize(("sql", "expected"), PRESERVED_CASES)
def test_normalization_preserves_valid_sql(sql: str, expected: str) -> None:
    assert normalize(sql) == expected


# ── Statements the routine must rewrite ─────────────────────────────────────


COLLAPSED_CASES = [
    ("SELECT * FROM db.default.t", "SELECT * FROM db.t"),
    ("SELECT * FROM mydb.default.orders", "SELECT * FROM mydb.orders"),
    ("SELECT * FROM `mydb`.`default`.`orders`", "SELECT * FROM `mydb`.`orders`"),
    # Case-insensitive keyword and placeholder, as the engine is.
    ("select * from mydb.DEFAULT.orders", "select * from mydb.orders"),
    ("SELECT * FROM MYDB.default.ORDERS", "SELECT * FROM MYDB.ORDERS"),
    # Interior whitespace around the dots is layout, and the collapsed form is
    # emitted in the canonical `db.table` shape.
    ("SELECT * FROM mydb . default . orders", "SELECT * FROM mydb.orders"),
    # JOIN and other table-introducing positions.
    (
        "SELECT * FROM mydb.default.orders JOIN x.default.y ON 1 = 1",
        "SELECT * FROM mydb.orders JOIN x.y ON 1 = 1",
    ),
    ("INSERT INTO mydb.default.orders SELECT 1", "INSERT INTO mydb.orders SELECT 1"),
    ("UPDATE mydb.default.orders SET x = 1", "UPDATE mydb.orders SET x = 1"),
    # A literal or comment elsewhere in the statement must not disable the
    # rewrite of a genuine table reference.
    (
        "SELECT * FROM a.default.b WHERE note = 'x.default.y'",
        "SELECT * FROM a.b WHERE note = 'x.default.y'",
    ),
    (
        "SELECT * FROM a.default.b -- @stage1.d.default.e",
        "SELECT * FROM a.b -- @stage1.d.default.e",
    ),
    # Two references in one statement, rewritten independently.
    (
        "SELECT * FROM a.default.b, c.default.d",
        "SELECT * FROM a.b, c.d",
    ),
    # DESCRIBE/DESC targets: the target follows the statement keyword, so the
    # table-reference anchor above does not reach it. Without this anchor the
    # statement reached StarRocks with the placeholder and failed with a syntax
    # error at the first dot (the assistant's ``DESCRIBE
    # NOVA_ANALYTICS.default.channel_performance`` regression).
    (
        "DESCRIBE NOVA_ANALYTICS.default.channel_performance",
        "DESCRIBE NOVA_ANALYTICS.channel_performance",
    ),
    ("DESC mydb.default.orders", "DESC mydb.orders"),
    ("describe nova_analytics.default.t", "describe nova_analytics.t"),
    # A catalogue-qualified target keeps its catalogue; only the placeholder
    # `db.default.table` span is collapsed.
    (
        "DESCRIBE default_catalog.NOVA_ANALYTICS.default.channel_performance",
        "DESCRIBE default_catalog.NOVA_ANALYTICS.channel_performance",
    ),
    ("DESC `mydb`.`default`.`orders`", "DESC `mydb`.`orders`"),
    # A DESCRIBE later in a multi-statement script is rewritten too.
    ("select 1; describe db.default.t;", "select 1; describe db.t;"),
    # A trailing comment must not disable the rewrite.
    ("DESCRIBE db.default.t -- comment", "DESCRIBE db.t -- comment"),
]


@pytest.mark.parametrize(("sql", "expected"), COLLAPSED_CASES)
def test_normalization_collapses_table_reference_placeholder(sql: str, expected: str) -> None:
    assert normalize(sql) == expected


def test_normalization_is_idempotent() -> None:
    """Running twice must equal running once.

    The routine is applied on both ``execute`` and ``explain``; a non-idempotent
    rewrite would make the second pass corrupt what the first produced.
    """
    for sql, _ in COLLAPSED_CASES + PRESERVED_CASES:
        once = normalize(sql)
        assert normalize(once) == once, sql


def test_normalization_does_not_rewrite_stage_path_when_used_as_table() -> None:
    """A stage reference in table position must still be left intact.

    ``FROM @stage1.data.default.csv`` reaches both the table-reference anchor and
    the stage path; the mask has to win, or the file the user named silently
    changes.
    """
    sql = "SELECT * FROM @stage1.data.default.csv"
    assert normalize(sql) == sql


def test_normalization_rewrites_comma_separated_table_list() -> None:
    """A second table in a FROM list is a table position, not a column path.

    Pinned because it is the case that separates "positional anchor" from
    "keyword anchor": without the comma alternative this silently left
    ``c.default.d`` untouched and sent an unrewritten placeholder to the engine.
    """
    sql = "SELECT * FROM a.default.b, c.default.d"
    assert normalize(sql) == "SELECT * FROM a.b, c.d"


def test_normalization_known_limitation_comma_after_open_paren() -> None:
    """Documents the one accepted over-rewrite, and what is *not* over-rewritten.

    The comma anchor cannot tell a FROM-list comma from a function-argument comma
    without real clause tracking. Inside ``f(a.default.b, c.default.d)`` the
    argument after ``(`` is correctly left alone — ``(`` is not a table
    position — but the argument after the comma *is* collapsed, because a comma
    is. Asserted so the limitation is visible rather than latent; the fix, if it
    ever matters, is the parser (NOVA-17) rather than a cleverer regex.
    """
    sql = "SELECT f(a.default.b, c.default.d)"
    assert normalize(sql) == "SELECT f(a.default.b, c.d)"


# ── The routine must never weaken the guard ─────────────────────────────────
#
# It runs *before* ``guard_sql`` on both the execute and explain paths, so it
# sees and rewrites the text the guard will later read. If the rewrite could
# turn a blocked statement into an allowed one, the corruption fixed above would
# have been traded for a security hole. Pinned explicitly (STANDARD SS12
# rule 2 — write the test where the defect class lives).

BLOCKED_STATEMENTS = [
    "DROP ROLE ACCOUNTADMIN",
    "REVOKE ALL ON *.* FROM ROLE ACCOUNTADMIN",
    "ALTER ROLE ACCOUNTADMIN",
    "DROP /* ; */ ROLE ACCOUNTADMIN",
]


@pytest.mark.parametrize("sql", BLOCKED_STATEMENTS)
def test_normalization_cannot_allow_a_blocked_statement(sql: str) -> None:
    with pytest.raises(ForbiddenSQLError):
        guard_sql(normalize(sql))


def test_normalization_still_rewrites_table_in_destructive_statement() -> None:
    """A DROP carrying the placeholder is rewritten, not rejected.

    Destructive confirmation is a separate control (``is_destructive_sql``); the
    normalizer's only job is to hand the guard engine-shaped SQL. Pinned so a
    future change cannot quietly make the placeholder a guard failure.
    """
    assert normalize("DROP TABLE db.default.t") == "DROP TABLE db.t"
