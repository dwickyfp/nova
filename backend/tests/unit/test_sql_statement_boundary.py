"""Regression tests for the statement boundary `guard_sql` splits on.

`guard_sql` anchors its patterns per statement (NOVA-13 T-1), so the splitter it
uses *is* part of the guard: a boundary the engine does not have hands the
patterns fragments the engine never sees, and a boundary the engine *does* have
that the splitter misses would leave a statement unguarded.

`split_sql_statements` originally tracked only single-quoted strings, so a ``;``
inside a ``/* ... */`` block comment was read as a separator. The engine does not
treat it as one, which cut both ways:

* **bypass** — ``DROP /*;*/ ROLE ACCOUNTADMIN`` split into ``DROP /*`` and
  ``*/ ROLE ACCOUNTADMIN``, so no pattern ever saw the keywords adjacent and the
  ``_GAP`` tolerance written for that exact shape was defeated;
* **over-block** — ``SELECT /* ; DROP ROLE ACCOUNTADMIN */ 2`` was blocked on
  text that lives inside a comment the engine discards.

The engine behaviour pinned here was measured against a live StarRocks:

    SELECT /* ; */ 42                            -> ((42,),)   one statement
    SELECT 'before' /* a /* b */ , 'after'       -> (('before','after'),)
    SELECT 1 /* a /* b */ + 2                    -> ((3,),)
    DROP /*;*/ ROLE <throwaway>                  -> executes as one DROP ROLE

Unit-level: no engine needed to run these.
"""

import pytest

from app.common.sql_guard import (
    guard_sql,
    is_destructive_sql,
    normalize_sql,
    split_sql_statements,
)
from app.core.exceptions import ForbiddenSQLError


class TestSplitterUnderstandsBlockComments:
    """A ``;`` inside ``/* */`` is comment text, not a statement boundary."""

    @pytest.mark.parametrize(
        ("sql", "expected"),
        [
            # The engine reads each of these as ONE statement.
            ("SELECT /* ; */ 42", ["SELECT /* ; */ 42"]),
            (
                "SELECT /* ; DROP ROLE ACCOUNTADMIN */ 42",
                ["SELECT /* ; DROP ROLE ACCOUNTADMIN */ 42"],
            ),
            ("SELECT /* a /* b */ 7", ["SELECT /* a /* b */ 7"]),
            ("SELECT 1 /* a /* b */ + 2", ["SELECT 1 /* a /* b */ + 2"]),
            ("DROP /*;*/ ROLE analyst", ["DROP /*;*/ ROLE analyst"]),
            # A real separator still separates.
            ("SELECT 1; SELECT 2", ["SELECT 1", "SELECT 2"]),
            ("SELECT 1; SELECT /* ; */ 2", ["SELECT 1", "SELECT /* ; */ 2"]),
            # Semicolon after the comment closes is a real boundary, because the
            # engine closes the comment at the first `*/`. This is the direction
            # that must NOT over-swallow.
            ("SELECT /* a /* b */ ; SELECT 2", ["SELECT /* a /* b */", "SELECT 2"]),
            (
                "SELECT 1; SELECT 2; DROP ROLE analyst",
                ["SELECT 1", "SELECT 2", "DROP ROLE analyst"],
            ),
        ],
    )
    def test_boundary_matches_engine(self, sql, expected):
        assert split_sql_statements(sql) == expected

    def test_semicolon_inside_literal_still_not_a_boundary(self):
        assert split_sql_statements("SELECT 'a;b'") == ["SELECT 'a;b'"]
        assert split_sql_statements("SELECT 'it''s;ok'") == ["SELECT 'it''s;ok'"]

    def test_semicolon_inside_line_comment_not_a_boundary(self):
        # `--` runs to end of line, so the `;` in it is text.
        assert split_sql_statements("SELECT 1 -- c;\n; SELECT 2") == [
            "SELECT 1 -- c;",
            "SELECT 2",
        ]

    def test_quote_inside_block_comment_does_not_open_a_literal(self):
        # An odd number of quotes inside a comment must not desynchronise the
        # scanner and hide the following `;`.
        assert split_sql_statements("SELECT /* it's */ 1; SELECT 2") == [
            "SELECT /* it's */ 1",
            "SELECT 2",
        ]

    def test_double_dash_inside_block_comment_is_text(self):
        assert split_sql_statements("SELECT /* -- */ 1; SELECT 2") == [
            "SELECT /* -- */ 1",
            "SELECT 2",
        ]

    def test_unterminated_block_comment_swallows_the_rest(self):
        # Matches strip_sql_comments: an unterminated comment eats the remainder,
        # so the later `;` is not a boundary the engine would honour. The comment
        # region stays attached to the statement it opened in.
        assert split_sql_statements("SELECT 1; SELECT /* unterminated") == [
            "SELECT 1",
            "SELECT /* unterminated",
        ]

    def test_nested_open_marker_does_not_extend_the_comment(self):
        # The first `*/` closes, so the `;` after it IS a real boundary.
        assert split_sql_statements("SELECT /* a /* b */ ; SELECT 2") == [
            "SELECT /* a /* b */",
            "SELECT 2",
        ]


class TestSplitterAgreesWithStripSqlComments:
    """The splitter and the normalizer must agree on comment extent.

    They are two views of the same engine rule. If the splitter thought a
    comment ended somewhere other than where ``strip_sql_comments`` ends it, one
    of them would be describing SQL the engine never sees.
    """

    @pytest.mark.parametrize(
        "sql",
        [
            "DROP /* a /* b */ ROLE ACCOUNTADMIN",
            "SELECT /* ; */ 42",
            "SELECT 1; DROP /*;*/ ROLE ACCOUNTADMIN",
            "SELECT 'before' /* a /* b */ , 'after'",
            "SELECT /* -- */ 1",
            "SELECT /* it's */ 1",
            "SELECT 1 /* a /* b */ + 2",
        ],
    )
    def test_no_comment_text_is_lost_or_boundary_shifted(self, sql):
        """Splitting must be a pure partition on real separators.

        Every piece rejoined with a single space must normalize to the same text
        as the original: the splitter neither drops comment content nor moves a
        boundary the comment model would not honour. This is the property that
        makes the two functions two views of one rule.
        """
        pieces = split_sql_statements(sql)
        # The rejoined form may differ in whitespace around a restored separator
        # (`;` vs ` ; `), which normalization preserves, so compare on a form with
        # all whitespace removed: what matters is that no comment text vanished or
        # crossed a boundary.
        joined = normalize_sql(" ; ".join(pieces)).replace(" ", "")
        original = normalize_sql(sql).replace(" ", "")
        assert joined == original

    def test_comment_only_difference_does_not_change_statement_count(self):
        # The same script with and without a comment-hidden `;` is one statement
        # each; only a real `;` adds one.
        assert len(split_sql_statements("SELECT 1")) == 1
        assert len(split_sql_statements("SELECT /* ; */ 1")) == 1
        assert len(split_sql_statements("SELECT 1; SELECT 2")) == 2


class TestForbiddenStatementCannotBeAssembledFromFragments:
    """Finding 2a — the bypass the weak splitter opened.

    ``DROP /*;*/ ROLE ACCOUNTADMIN`` is one statement to the engine. Splitting it
    at the comment's ``;`` gave the guard ``DROP /*`` and ``*/ ROLE
    ACCOUNTADMIN``, so the ``DROP{_GAP}ROLE{_GAP}ACCOUNTADMIN`` pattern — whose
    ``_GAP`` tolerates a stray ``*/`` — never saw the keywords adjacent.
    """

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 1; DROP /*;*/ ROLE ACCOUNTADMIN",
            "SELECT 1; ALTER /* ; */ ROLE ACCOUNTADMIN SET DEFAULT ROLE NONE",
            "SELECT 1; REVOKE /*;*/ ALL ON *.* FROM ROLE ACCOUNTADMIN",
            "SELECT 1; REVOKE /*;*/ ALL ON *.* FROM ACCOUNTADMIN",
            "SELECT 1; DROP /*;*/ USER root",
            "SELECT 1; DROP /*;*/ ROLE `ACCOUNTADMIN`",
            "CREATE TABLE t (id INT); DROP /*;*/ ROLE ACCOUNTADMIN",
            "SELECT 1; DROP /* ; DROP ROLE ACCOUNTADMIN ; */ ROLE ACCOUNTADMIN",
        ],
    )
    def test_comment_hidden_separator_cannot_smuggle_a_forbidden_statement(self, sql):
        with pytest.raises(ForbiddenSQLError):
            guard_sql(sql)

    def test_the_shape_the_guard_sees_is_the_shape_the_engine_parses(self):
        # Both halves of the payload land in one statement, which is the whole
        # point: the pattern can see them adjacent.
        assert split_sql_statements("DROP /*;*/ ROLE ACCOUNTADMIN") == [
            "DROP /*;*/ ROLE ACCOUNTADMIN"
        ]
        assert normalize_sql("DROP /*;*/ ROLE ACCOUNTADMIN") == "DROP ROLE ACCOUNTADMIN"

    def test_fragment_leftover_marker_still_tolerated(self):
        # The NOVA-12 shape: the comment closes at the first `*/`, leaving a
        # marker in the normalized text. `_GAP` must still bridge it.
        assert normalize_sql("DROP /* a /* b */ ROLE ACCOUNTADMIN") == "DROP */ ROLE ACCOUNTADMIN"
        with pytest.raises(ForbiddenSQLError, match="ACCOUNTADMIN"):
            guard_sql("DROP /* a /* b */ ROLE ACCOUNTADMIN")


class TestCleanScriptIsNotBlockedByCommentText:
    """Finding 2b — the over-block the weak splitter introduced.

    A comment containing forbidden-looking text is not a forbidden statement: the
    engine discards the comment, and ``normalize_sql`` discards it too. Splitting
    inside the comment exposed that text as if it were SQL.
    """

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 1; SELECT /* ; DROP ROLE ACCOUNTADMIN */ 2",
            "SELECT /* ; DROP ROLE ACCOUNTADMIN */ 2",
            "SELECT /* DROP ROLE ACCOUNTADMIN */ 1",
            "SELECT /* REVOKE ALL ON *.* FROM ROLE ACCOUNTADMIN */ 1",
            "SELECT /* DROP USER root */ 1",
            "SELECT 1 /* a /* b */ + 2",
            "SELECT 'before' /* a /* b */ , 'after'",
            "SELECT /* ; */ 42",
        ],
    )
    def test_comment_content_does_not_trigger_the_guard(self, sql):
        guard_sql(sql)

    def test_comment_only_statement_is_not_destructive(self):
        # The comment is all there is, so there is no statement to confirm.
        assert is_destructive_sql("/* DROP TABLE t */") is False
        assert is_destructive_sql("SELECT /* DROP TABLE t */ 1") is False

    def test_real_destructive_statement_after_a_comment_still_confirms(self):
        assert is_destructive_sql("/* c */ DROP TABLE t") is True
        assert is_destructive_sql("DROP TABLE t") is True

    def test_destructive_detection_is_anchored_to_the_first_statement(self):
        # `DESTRUCTIVE_SQL_PATTERN` is `^`-anchored, so it reports on the leading
        # statement only — unchanged by the splitter work, and the reason
        # `QueryService.execute` confirms per statement rather than on the blob.
        assert is_destructive_sql("SELECT 1; DROP TABLE t") is False
        assert is_destructive_sql("DROP TABLE t; SELECT 1") is True


class TestGuaranteesPreserved:
    """The boundaries that were already right must stay right."""

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 1",
            "SELECT 1; SELECT 2",
            "DROP ROLE analyst",
            "REVOKE ALL ON *.* FROM ROLE analyst",
            "REVOKE ALL ON *.* FROM ROLE 'analyst'",
            "DROP ROLE IF EXISTS analyst",
            "CREATE ROLE analyst",
            "SELECT 'a;b'",
            "GRANT SELECT ON db.t TO ROLE analyst",
            "",
            "   \n\t ",
            ";",
            ";;",
            "-- only a comment",
            "/* only a comment */",
        ],
    )
    def test_still_allowed(self, sql):
        guard_sql(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            "DROP ROLE ACCOUNTADMIN",
            "REVOKE ALL ON *.* FROM ROLE ACCOUNTADMIN",
            "ALTER ROLE ACCOUNTADMIN RENAME TO evil",
            "DROP USER root",
            "DROP GLOBAL FUNCTION AI_COMPLETE(STRING)",
            "DROP /* a /* b */ ROLE ACCOUNTADMIN",
            "SELECT 1; DROP ROLE ACCOUNTADMIN",
        ],
    )
    def test_still_blocked(self, sql):
        with pytest.raises(ForbiddenSQLError):
            guard_sql(sql)

    def test_splitting_a_split_statement_is_idempotent(self):
        # query/service.py:89-91 splits, then guard_sql splits each piece again.
        for sql in [
            "SELECT 1; DROP TABLE t; SELECT 2",
            "SELECT /* ; */ 42",
            "DROP /* a /* b */ ROLE ACCOUNTADMIN",
            "REVOKE ALL ON *.* FROM ROLE 'ACCOUNTADMIN'",
        ]:
            for piece in split_sql_statements(sql):
                assert split_sql_statements(piece) == [piece], piece

    def test_empty_input_yields_no_statements(self):
        assert split_sql_statements("") == []
        assert split_sql_statements("   ") == []
        assert split_sql_statements(";") == []
        assert split_sql_statements(";;;") == []
