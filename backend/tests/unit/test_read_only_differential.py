"""Differential test: the lexical read-only rules against the grammar effects.

The assistant, semantic views, agent artifacts and the MySQL proxy's
``READ ONLY`` transactions all ask :func:`policy.is_allowed_statement`. The
decision comes from the grammar frontend's effects
(:func:`app.sql_frontend.read_only.is_read_only_statement`); the keyword rules
(:func:`policy.lexically_allowed`) may only deny on top of it.

This runs both classifiers over the grammar corpus, the functional SQL
scenarios and the statements the policy tests use, and checks every place they
disagree:

* where the keyword rules allow and the effects do not, the statement must
  really change something or fail to parse; the combined policy refuses it.
  The audit's ``WITH … DELETE`` bypasses are in this group;
* where the keyword rules refuse and the effects allow, the combined policy
  still refuses: the keyword layer is a deny layer.
"""

from __future__ import annotations

import pytest

from app.common.sql_guard import split_sql_statements
from app.modules.assistant.tools import policy
from app.sql_frontend.read_only import is_read_only_statement, reads_only, statement_effects
from tests.integration.functional_sql.scenarios import core_cases
from tests.unit import test_sql_dialect_nova_corpus as nova_corpus

#: Read-only bypasses of the keyword rules (architecture audit, section 3.1).
#: Each is a DELETE: the CTE header hides a ``)SELECT`` from the keyword scan
#: in a double-quoted string, a backquoted identifier or a comment.
BYPASSES = (
    'WITH x AS (SELECT ")SELECT" AS a) DELETE FROM db.t WHERE 1=1',
    "WITH x AS (SELECT `)SELECT` FROM db.u) DELETE FROM db.t WHERE 1=1",
    "WITH x AS (SELECT 1 /* )SELECT */) DELETE FROM db.t WHERE 1=1",
)

#: Statements the policy tests and the proxy tests classify.
POLICY_CASES = (
    "SELECT 1",
    "SELECT * FROM t",
    "SELECT 'INTO OUTFILE' AS note",
    "SELECT * FROM `files`",
    "SHOW TABLES",
    "SHOW DATABASES",
    "SHOW CREATE TABLE db.t",
    "SHOW GRANTS",
    "DESCRIBE db.t",
    "DESC t",
    "LIST @mystage",
    "LIST FILES @mystage/dir/",
    "SELECT * FROM @mystage/a.csv",
    "EXPLAIN SELECT 1",
    "EXPLAIN ANALYZE SELECT * FROM t",
    "EXPLAIN WITH x AS (SELECT 1) SELECT * FROM x",
    "EXPLAIN DELETE FROM t WHERE 1=1",
    "EXPLAIN DROP TABLE t",
    "EXPLAIN INSERT INTO t SELECT 1",
    "WITH x AS (SELECT 1) SELECT * FROM x",
    "WITH RECURSIVE x AS (SELECT 1) SELECT * FROM x",
    "WITH a AS (SELECT '(') , b AS (SELECT 2) SELECT * FROM a, b",
    "WITH x AS (SELECT 1) DELETE FROM t WHERE id IN (SELECT * FROM x)",
    "WITH x AS (SELECT 1) INSERT INTO t SELECT * FROM x",
    "WITH x AS (SELECT 1) UPDATE t SET a = 1",
    "(SELECT 1)",
    "SELECT 1 UNION ALL SELECT 2",
    "SELECT 1 INTO OUTFILE 's3://b/x' FORMAT AS CSV",
    "SELECT 1 INTO OUTFILE '@exports/x/' FORMAT AS CSV",
    "INSERT INTO FILES('path'='@exports/x/','format'='parquet') SELECT 1",
    "INSERT INTO t VALUES (1)",
    "INSERT OVERWRITE t SELECT 1",
    "UPDATE t SET a = 1",
    "DELETE FROM t WHERE 1=1",
    "TRUNCATE TABLE t",
    "DROP TABLE t",
    "ALTER TABLE t DROP COLUMN a",
    "CREATE TABLE t (a INT)",
    "CREATE ML_MODEL m TYPE = FORECAST",
    "CREATE TASK t AS SELECT 1",
    "GRANT SELECT ON db.t TO ROLE r",
    "SET ROLE r",
    "USE db",
    "ADMIN SHOW FRONTEND CONFIG",
    "SHOW VIEWS",
    "",
    *BYPASSES,
)


def _corpus() -> list[str]:
    statements: list[str] = list(POLICY_CASES)
    statements.extend(nova_corpus.ENGINE_CASES)
    statements.extend(nova_corpus.NOVA_AI_CASES)
    statements.extend(nova_corpus.NOVA_GRAMMAR_CASES)
    statements.append(nova_corpus.DEFAULT_SCHEMA_CASE)
    statements.extend(sql for sql, _ in nova_corpus.MALFORMED_CASES)
    for case in core_cases("nova_differential"):
        for text in (*case.setup, case.sql):
            statements.extend(split_sql_statements(text) or [text])
    return list(dict.fromkeys(statements))


CORPUS = _corpus()


def test_corpus_is_large_enough_to_mean_something():
    assert len(CORPUS) >= 150


@pytest.mark.parametrize("sql", CORPUS)
def test_policy_never_allows_what_the_grammar_effects_refuse(sql):
    allowed = policy.is_allowed_statement(sql)
    assert allowed == (policy.lexically_allowed(sql) and is_read_only_statement(sql))
    if allowed:
        effects = statement_effects(sql)
        assert effects is not None and reads_only(effects)


def test_every_divergence_is_explained():
    lexical_only = []
    effects_only = []
    for sql in CORPUS:
        lexical, effects = policy.lexically_allowed(sql), is_read_only_statement(sql)
        if lexical and not effects:
            lexical_only.append(sql)
        elif effects and not lexical:
            effects_only.append(sql)

    # Keyword rules said "read-only", the grammar disagreed: each one changes
    # something or is not valid SQL, and the policy refuses it.
    for sql in lexical_only:
        found = statement_effects(sql)
        assert found is None or not reads_only(found), sql
        assert policy.is_allowed_statement(sql) is False, sql
    assert set(BYPASSES) <= set(lexical_only)

    # The keyword layer refused a grammar read: it stays refused.
    for sql in effects_only:
        assert policy.is_allowed_statement(sql) is False, sql
    assert "EXPLAIN DELETE FROM t WHERE 1=1" in effects_only


@pytest.mark.parametrize("sql", BYPASSES)
def test_with_delete_bypasses_are_not_read_only(sql):
    assert policy.is_allowed_statement(sql) is False
    assert policy.tool_classification(sql) == "denied"
    classification, decisions = policy.classify_statements([sql])
    assert classification == "denied"
    assert decisions[0].reason == "The statement deletes rows and is not read-only."
    effects = statement_effects(sql)
    assert effects is not None and effects.deletes_rows


@pytest.mark.parametrize("sql", BYPASSES)
def test_bypass_behind_a_read_is_a_mixed_refusal(sql):
    classification, decisions = policy.classify_statements(["SELECT 1", sql])
    assert classification == "destructive"
    assert [decision.allowed for decision in decisions] == [True, False]


def test_unparseable_statement_fails_closed():
    assert statement_effects("SELECT FROM WHERE") is None
    assert policy.is_allowed_statement("SELECT FROM WHERE") is False
    assert (
        policy.denial_reason("SELECT FROM WHERE")
        == "The statement could not be classified as read-only."
    )
