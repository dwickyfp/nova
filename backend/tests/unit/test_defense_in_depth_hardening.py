"""Regressions for statement guards, role metadata, and identifier normalization.

Native role mutations are retired; Ranger owns the administration checks.
"""

import pytest

from app.common.sql_guard import (
    guard_sql,
    split_sql_statements,
    unquote_identifier,
)
from app.core.exceptions import ForbiddenSQLError
from app.modules.users.service import UserService

# ── T-1: statement anchoring ────────────────────────────────────────────────


class TestT1GuardAnchorsEachStatement:
    """``guard_sql`` must see every statement, not just the one it starts in.

    The API accepts multi-statement scripts, and eight call sites
    (``tables/router.py:98,150,167``, ``views/router.py:62,92,110``,
    ``query/service.py:550``, plus the ``execute()`` path) hand the guard a raw
    blob. A guard anchored on the whole blob is blind to everything after the
    first ``;``, so the ACCOUNTADMIN patterns simply do not run there.
    """

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 1; DROP ROLE ACCOUNTADMIN",
            "SELECT 1; REVOKE ALL ON *.* FROM ROLE ACCOUNTADMIN",
            "SELECT 1; ALTER ROLE ACCOUNTADMIN RENAME TO evil",
            "SELECT 1; DROP USER root",
            "SELECT 1; DROP GLOBAL FUNCTION AI_COMPLETE(STRING)",
            "/* lead */ SELECT 1; /* mid */ DROP ROLE `ACCOUNTADMIN`",
            "CREATE ROLE analyst; DROP ROLE ACCOUNTADMIN",
            "SELECT 1; SELECT 2; DROP ROLE ACCOUNTADMIN",
            "SELECT 1;\nDROP ROLE ACCOUNTADMIN;",
        ],
    )
    def test_blocked_operation_in_later_statement_is_seen(self, sql):
        with pytest.raises(ForbiddenSQLError):
            guard_sql(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 1; DROP ROLE ACCOUNTADMIN",
            "SELECT 1; REVOKE ALL ON *.* FROM ROLE ACCOUNTADMIN",
        ],
    )
    def test_later_statement_reports_the_matching_message(self, sql):
        # The message must come from the statement that is actually forbidden,
        # not from a mis-anchored match on the first one.
        with pytest.raises(ForbiddenSQLError, match="ACCOUNTADMIN"):
            guard_sql(sql)

    def test_single_statement_behaviour_is_unchanged(self):
        # Anchoring must not alter what a lone statement does.
        with pytest.raises(ForbiddenSQLError, match="ACCOUNTADMIN"):
            guard_sql("DROP ROLE ACCOUNTADMIN")
        guard_sql("SELECT 1")

    def test_splitting_a_split_statement_is_idempotent(self):
        """``query/service.py:89-91`` splits, then guards each piece.

        Guarding a piece must not split it again into something different — the
        reason ``guard_sql`` can safely anchor without the caller opting out is
        that ``;`` outside a string literal is the only separator and a single
        statement has none.
        """
        statements = split_sql_statements("SELECT 1; DROP TABLE t; SELECT 2")
        assert statements == ["SELECT 1", "DROP TABLE t", "SELECT 2"]
        for statement in statements:
            assert split_sql_statements(statement) == [statement]

    def test_query_service_per_statement_guard_still_blocks(self):
        # Reproduces the exact shape of query/service.py:89-91.
        sql = "SELECT 1; DROP ROLE ACCOUNTADMIN"
        statements = split_sql_statements(sql) or [sql]
        with pytest.raises(ForbiddenSQLError, match="ACCOUNTADMIN"):
            for statement in statements:
                guard_sql(statement)

    def test_semicolon_inside_literal_is_not_a_boundary(self):
        # A `;` in a literal must not create a phantom statement whose tail is
        # then guarded out of context.
        guard_sql("SELECT 'a;b'")
        with pytest.raises(ForbiddenSQLError, match="ACCOUNTADMIN"):
            guard_sql("SELECT 'a;b'; DROP ROLE ACCOUNTADMIN")


class TestT1OverBlockingControls:
    """The anchor must not turn legitimate scripts into violations."""

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 1",
            "SELECT 1; SELECT 2",
            "SELECT 1; SELECT 2; SELECT 3",
            "DROP ROLE analyst; CREATE ROLE analyst",
            "REVOKE ALL ON *.* FROM ROLE analyst",
            "REVOKE ALL ON *.* FROM ROLE 'analyst'; GRANT SELECT ON t TO ROLE analyst",
            "SELECT 'ACCOUNTADMIN'",
            "CREATE TABLE t (id INT); DROP TABLE t",
            "",
            "   \n\t ",
            ";",
            ";;",
        ],
    )
    def test_legitimate_scripts_still_allowed(self, sql):
        guard_sql(sql)

    def test_safe_script_naming_accountadmin_as_a_literal_is_allowed(self):
        """A script where ACCOUNTADMIN appears only as data is not a violation.

        ``REVOKE ... FROM ROLE analyst; SELECT 'ACCOUNTADMIN'`` names a safe
        role in the REVOKE and a string literal in the SELECT. Both statements
        are clean, so the script is clean — rejecting it would be over-blocking.
        What the anchor guarantees is that the *reverse* — the forbidden
        statement hidden behind a safe first one — is caught, which
        ``TestT1GuardAnchorsEachStatement`` covers.
        """
        guard_sql("REVOKE ALL ON *.* FROM ROLE analyst; SELECT 'ACCOUNTADMIN'")

    def test_forbidden_statement_cannot_hide_behind_a_safe_revoke(self):
        # The mirror of the case above, and the one that matters: same prefix,
        # but the tail is a real REVOKE on ACCOUNTADMIN.
        with pytest.raises(ForbiddenSQLError, match="ACCOUNTADMIN"):
            guard_sql(
                "REVOKE ALL ON *.* FROM ROLE analyst; "
                "REVOKE ALL ON *.* FROM ROLE ACCOUNTADMIN"
            )


# ── T-2: role-name normalization ────────────────────────────────────────────


class TestT2RoleFlags:
    """Role metadata preserves ACCOUNTADMIN protection after native mutation removal."""

    @pytest.fixture
    def svc(self) -> UserService:
        return UserService()

    def test_role_flags_marks_quoted_accountadmin_protected(self, svc):
        # The UI reads these flags to decide whether to offer a Delete button;
        # a quoted spelling must not render the role deletable.
        for spelling in ["ACCOUNTADMIN", "'ACCOUNTADMIN'", "`ACCOUNTADMIN`", '"ACCOUNTADMIN"']:
            flags = svc._role_flags(spelling)
            assert flags["is_protected"] is True, spelling
            assert flags["is_mutable"] is False, spelling

    def test_role_flags_leaves_ordinary_role_mutable(self, svc):
        flags = svc._role_flags("analyst")
        assert flags["is_protected"] is False
        assert flags["is_builtin"] is False
        assert flags["is_mutable"] is True


class TestT2UnquoteIdentifier:
    """The shared normalization helper the two controls sit on."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("ACCOUNTADMIN", "ACCOUNTADMIN"),
            ("'ACCOUNTADMIN'", "ACCOUNTADMIN"),
            ("`ACCOUNTADMIN`", "ACCOUNTADMIN"),
            ('"ACCOUNTADMIN"', "ACCOUNTADMIN"),
            ("[ACCOUNTADMIN]", "ACCOUNTADMIN"),
            ("  ACCOUNTADMIN  ", "ACCOUNTADMIN"),
            ("  'ACCOUNTADMIN'  ", "ACCOUNTADMIN"),
            ("analyst", "analyst"),
            ("", ""),
            ("   ", ""),
            ("db.schema.tbl", "db.schema.tbl"),
            ("`db.schema.tbl`", "db.schema.tbl"),
        ],
    )
    def test_collapses_identifier_quoting(self, value, expected):
        assert unquote_identifier(value) == expected

    @pytest.mark.parametrize(
        "value",
        [
            "'ACCOUNT ADMIN'",  # a literal body with a space is not an identifier
            "'ACCOUNT-ADMIN'",  # nor is one with punctuation
            "O'Brien",
            "'a b'",
        ],
    )
    def test_non_identifier_bodies_keep_their_quoting(self, value):
        # Mirrors `_QUOTED_IDENTIFIER`, which only strips identifier-shaped
        # bodies, so a real string literal stays visible as data.
        assert unquote_identifier(value) == value
