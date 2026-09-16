"""Regression tests for SQL guard bypasses.

Each test here corresponds to a demonstrated bypass that was reproducible against
the previous implementation. They are unit-level: no engine, no network.
"""

import pytest

from app.common.sql_guard import (
    guard_sql,
    is_destructive_sql,
    is_unscoped_mutation,
    normalize_sql,
    split_sql_statements,
)
from app.core.exceptions import ForbiddenSQLError


class TestCommentBypass:
    """Bypass 1: an inline comment shifted the guard's regex off the keyword."""

    def test_block_comment_between_drop_and_role_blocked(self):
        with pytest.raises(ForbiddenSQLError, match="ACCOUNTADMIN"):
            guard_sql("DROP /*x*/ ROLE ACCOUNTADMIN")

    def test_block_comment_before_statement_blocked(self):
        with pytest.raises(ForbiddenSQLError, match="ACCOUNTADMIN"):
            guard_sql("/* lead */ DROP ROLE ACCOUNTADMIN")

    def test_line_comment_between_identical_keywords_is_stripped(self):
        # A line comment terminates at the newline, so the real statement after
        # it is still seen.
        with pytest.raises(ForbiddenSQLError, match="ACCOUNTADMIN"):
            guard_sql("-- harmless\nDROP ROLE ACCOUNTADMIN")

    def test_multi_line_block_comment_blocked(self):
        with pytest.raises(ForbiddenSQLError, match="ACCOUNTADMIN"):
            guard_sql("DROP /*\n multi\n line \n*/ ROLE ACCOUNTADMIN")

    def test_nested_comment_markers_do_not_escape(self):
        # StarRocks does not nest block comments, so this form is genuinely
        # `DROP */ ROLE ACCOUNTADMIN` to the engine — `*/` closes the comment and
        # the leftover marker is part of the statement. It is blocked because the
        # guard sees the real keyword/identifier pair, not a balanced pair of
        # markers that hid them.
        with pytest.raises(ForbiddenSQLError, match="ACCOUNTADMIN"):
            guard_sql("DROP /* /* nested */ */ ROLE ACCOUNTADMIN")

    @pytest.mark.parametrize(
        "sql",
        [
            "DROP ROLE /* a /* b */ ACCOUNTADMIN",
            "drop role /* a /* b */ ACCOUNTADMIN",
            "REVOKE /* a /* b */ ALL ON *.* FROM ROLE ACCOUNTADMIN",
            "ALTER /* a /* b */ ROLE ACCOUNTADMIN SET DEFAULT ROLE NONE",
            "ALTER ROLE evil /* a /* b */ RENAME TO ACCOUNTADMIN",
        ],
    )
    def test_single_close_marker_comment_blocked(self, sql):
        # The comment closes at its only `*/`, so `ACCOUNTADMIN` is live SQL to
        # the engine and must still be caught even though the scanner keeps the
        # unterminated remainder hidden.
        with pytest.raises(ForbiddenSQLError, match="ACCOUNTADMIN"):
            guard_sql(sql)

    def test_leftover_marker_does_not_hide_the_blocked_statement(self):
        # Closing at the first `*/` is the engine's rule; the leftover `*/` stays
        # in the normalized text and every pattern tolerates it, so the statement
        # reaches the guard instead of being dropped as unreachable comment text.
        assert normalize_sql("DROP ROLE /* a /* b */ ACCOUNTADMIN") == "DROP ROLE */ ACCOUNTADMIN"
        with pytest.raises(ForbiddenSQLError, match="ACCOUNTADMIN"):
            guard_sql("DROP ROLE /* a /* b */ ACCOUNTADMIN")

    @pytest.mark.parametrize(
        "sql",
        [
            # The marker in each inter-token position, so no single join in the
            # patterns can be the only one that tolerates it.
            "DROP /* a /* b */ ROLE ACCOUNTADMIN",
            "DROP ROLE IF /* a /* b */ EXISTS ACCOUNTADMIN",
            "REVOKE ALL ON *.* FROM /* a /* b */ ROLE ACCOUNTADMIN",
            "REVOKE ALL ON *.* /* a /* b */ FROM ROLE ACCOUNTADMIN",
            "ALTER ROLE /* a /* b */ ACCOUNTADMIN RENAME TO evil",
            "DROP USER root /* a /* b */",
            "DROP GLOBAL FUNCTION /* a /* b */ AI_COMPLETE(string)",
            # Leading marker, before any keyword.
            "/* /* */ DROP ROLE ACCOUNTADMIN",
        ],
    )
    def test_marker_in_every_position_blocked(self, sql):
        with pytest.raises(ForbiddenSQLError):
            guard_sql(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            # Multi-statement: the statement holding the blocked operation must
            # not be masked by a neighbouring one.
            "SELECT 1; DROP ROLE ACCOUNTADMIN /* trailing",
            "SELECT 1; /* a /* b */ DROP ROLE ACCOUNTADMIN",
        ],
    )
    def test_dropped_region_does_not_mask_a_neighbouring_statement(self, sql):
        with pytest.raises(ForbiddenSQLError, match="ACCOUNTADMIN"):
            guard_sql(sql)

    def test_unterminated_block_comment_swallows_remainder(self):
        # An unterminated comment leaves no SQL behind it — neither side of the
        # split is a complete statement. `strip_sql_comments` keeps its original
        # contract here; the enforced invariant is the guard's.
        from app.common.sql_guard import strip_sql_comments

        assert strip_sql_comments("DROP ROLE ACCOUNTADMIN /* trailing") == (
            "DROP ROLE ACCOUNTADMIN  "
        )
        guard_sql("SELECT 1 /* trailing")

    def test_complete_statement_before_unterminated_comment_still_blocked(self):
        with pytest.raises(ForbiddenSQLError, match="ACCOUNTADMIN"):
            guard_sql("DROP ROLE ACCOUNTADMIN /* trailing")

    def test_legitimate_query_with_single_close_comment_still_allowed(self):
        # StarRocks accepts `SELECT /* a /* b */ 1`; treating the first marker as
        # the closing one must not over-block a legitimate query.
        guard_sql("SELECT /* a /* b */ 1")
        guard_sql("SELECT 1 /* a /* b */")

    def test_comment_hidden_revoke_blocked(self):
        with pytest.raises(ForbiddenSQLError, match="revoke"):
            guard_sql("REVOKE /*c*/ ALL ON *.* FROM /*c*/ ROLE ACCOUNTADMIN")


class TestQuotedIdentifierBypass:
    """Bypass 2: backticked/quoted identifiers escaped the single-char patterns."""

    @pytest.mark.parametrize(
        "sql",
        [
            "REVOKE ALL ON *.* FROM ROLE `ACCOUNTADMIN`",
            'REVOKE ALL ON *.* FROM ROLE "ACCOUNTADMIN"',
            "REVOKE ALL ON *.* FROM ROLE [ACCOUNTADMIN]",
            "DROP ROLE `ACCOUNTADMIN`",
            'DROP ROLE "ACCOUNTADMIN"',
            "DROP ROLE [ACCOUNTADMIN]",
            "ALTER ROLE `ACCOUNTADMIN` RENAME TO admin",
        ],
    )
    def test_quoted_accountadmin_blocked(self, sql):
        with pytest.raises(ForbiddenSQLError, match="ACCOUNTADMIN"):
            guard_sql(sql)

    def test_revoke_without_role_keyword_blocked(self):
        # StarRocks also accepts REVOKE <priv> ON <obj> FROM <role>.
        with pytest.raises(ForbiddenSQLError, match="revoke"):
            guard_sql("REVOKE ALL ON *.* FROM ACCOUNTADMIN")

    def test_rename_to_accountadmin_blocked(self):
        with pytest.raises(ForbiddenSQLError):
            guard_sql("ALTER ROLE analyst RENAME TO ACCOUNTADMIN")

    def test_quoted_non_system_role_still_allowed(self):
        guard_sql("DROP ROLE `analyst`")


class TestCombinedBypasses:
    """The bypasses compose — a fix for one must not re-open another."""

    @pytest.mark.parametrize(
        "sql",
        [
            "DROP /*x*/ ROLE `ACCOUNTADMIN`",
            "SELECT 1; /*x*/ DROP ROLE `ACCOUNTADMIN`",
            "  \n\t DROP /*x*/ ROLE `ACCOUNTADMIN` ;",
        ],
    )
    def test_comment_plus_backtick_blocked(self, sql):
        with pytest.raises(ForbiddenSQLError, match="ACCOUNTADMIN"):
            for statement in split_sql_statements(sql) or [sql]:
                guard_sql(statement)


class TestNormalization:
    """Normalization must not corrupt string literals."""

    def test_comment_marker_inside_string_literal_preserved(self):
        # A `--` inside a literal is data, not a comment; stripping it would
        # truncate the statement.
        assert "a--b" in normalize_sql("SELECT 'a--b'")

    def test_block_comment_marker_inside_string_literal_preserved(self):
        assert "/*x*/" in normalize_sql("SELECT '/*x*/'")

    def test_escaped_quote_inside_literal_preserved(self):
        assert "it''s" in normalize_sql("SELECT 'it''s'")

    def test_semicolon_inside_literal_not_split(self):
        assert split_sql_statements("SELECT ';'") == ["SELECT ';'"]

    def test_comment_removal_does_not_join_tokens(self):
        # Removing a comment must leave a separator so `DROP` and `ROLE` do not
        # fuse into `DROPROLE`.
        assert "DROPROLE" not in normalize_sql("DROP/*c*/ROLE ACCOUNTADMIN")


class TestDestructiveDetectionPerStatement:
    """Bypass 3: a guard anchored to the whole blob missed later statements."""

    def test_leading_statement_is_not_destructive(self):
        assert is_destructive_sql("SELECT 1; DROP TABLE t") is False

    def test_split_exposes_the_destructive_second_statement(self):
        statements = split_sql_statements("SELECT 1; DROP TABLE t")
        assert statements == ["SELECT 1", "DROP TABLE t"]
        assert is_destructive_sql(statements[0]) is False
        assert is_destructive_sql(statements[1]) is True

    def test_second_statement_destructive_after_split(self):
        statements = split_sql_statements("SELECT 1; TRUNCATE TABLE t; SELECT 2")
        assert any(is_destructive_sql(s) for s in statements)

    def test_statement_destructive_behind_leading_comment(self):
        assert is_destructive_sql("/* comment */ DROP TABLE t") is True

    def test_plain_drop_still_destructive(self):
        assert is_destructive_sql("DROP TABLE t") is True

    def test_semicolon_inside_literal_does_not_create_statement(self):
        statements = split_sql_statements("SELECT 'a;b'")
        assert statements == ["SELECT 'a;b'"]
        assert all(is_destructive_sql(s) is False for s in statements)


class TestUnscopedMutation:
    """Bypass 4: `-- WHERE` made a WHERE-less DELETE look scoped."""

    def test_delete_with_where_only_in_comment_is_unscoped(self):
        assert is_unscoped_mutation("DELETE FROM t -- WHERE id=1") is True

    def test_update_with_where_only_in_comment_is_unscoped(self):
        assert is_unscoped_mutation("UPDATE t SET a=1 -- WHERE id=1") is True

    def test_delete_with_real_where_is_scoped(self):
        assert is_unscoped_mutation("DELETE FROM t WHERE id=1") is False

    def test_block_comment_where_is_unscoped(self):
        assert is_unscoped_mutation("DELETE FROM t /* WHERE id=1 */") is True

    def test_where_in_string_literal_is_unscoped(self):
        # `WHERE` inside a literal is data, not a clause.
        assert is_unscoped_mutation("DELETE FROM t WHERE a='x'") is False
        assert is_unscoped_mutation("DELETE FROM t -- 'WHERE'") is True


class TestExistingGuaranteesPreserved:
    """The pre-existing protections must survive the hardening."""

    def test_safe_select_passes(self):
        guard_sql("SELECT * FROM my_table")

    def test_safe_create_table_passes(self):
        guard_sql("CREATE TABLE test (id INT)")

    def test_drop_user_root_blocked(self):
        with pytest.raises(ForbiddenSQLError, match="root"):
            guard_sql("DROP USER root")

    def test_drop_other_user_allowed(self):
        guard_sql("DROP USER testuser")

    def test_drop_builtin_udf_blocked(self):
        with pytest.raises(ForbiddenSQLError):
            guard_sql("DROP GLOBAL FUNCTION AI_COMPLETE(string)")

    def test_empty_sql_passes(self):
        guard_sql("")

    def test_whitespace_only_sql_passes(self):
        guard_sql("   \n\t ")
