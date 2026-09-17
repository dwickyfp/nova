"""Regression tests for the ACCOUNTADMIN guard bypasses found by QA (NOVA-6).

Three independent root causes let ``ACCOUNTADMIN`` be dropped or revoked through
to the engine. Each has its own test class below, and the last class pins the
guarantees that must survive the fix — over-blocking is a failure, not safety.

Every case is a real ``guard_sql`` call. The SQL strings are synthetic; no
credentials appear in any fixture.

Baselines: ``61d67c7`` allowed all of ``TestSingleQuoteBypass`` and
``TestNewlineBypass``; ``TestIfExistsBypass`` is *latent* there (the guard
allowed it and only the unrelated destructive-statement confirmation stopped it,
so the ACCOUNTADMIN guard itself was blind).

``TestRevokeIfExistsBypass`` (NOVA-19) covers a regression introduced by the
``61d67c7`` fix itself: the ``IF EXISTS`` group was added to ``DROP ROLE`` and
``ALTER ROLE`` but missed on both ``REVOKE`` patterns, so every case in that
class was allowed on ``7b03cd9``.
"""

import pytest

from app.common.sql_guard import guard_sql, normalize_sql
from app.core.exceptions import ForbiddenSQLError


class TestSingleQuoteBypass:
    """Root cause 1 — ``_QUOTED_IDENTIFIER`` had no single-quote branch.

    StarRocks accepts a single-quoted identifier in the role position, so
    ``FROM ROLE 'ACCOUNTADMIN'`` left the quotes in place and the pattern saw
    ``ROLE 'ACCOUNTADMIN'`` — which never matches ``ROLE\\s+ACCOUNTADMIN``.
    """

    @pytest.mark.parametrize(
        "sql",
        [
            "REVOKE ALL ON *.* FROM ROLE 'ACCOUNTADMIN'",
            "DROP ROLE 'ACCOUNTADMIN'",
            "DROP ROLE IF EXISTS 'ACCOUNTADMIN'",
            "ALTER ROLE 'ACCOUNTADMIN' RENAME TO evil",
            "ALTER ROLE evil RENAME TO 'ACCOUNTADMIN'",
            "REVOKE SELECT ON *.* FROM ROLE 'accountadmin'",
        ],
    )
    def test_single_quoted_accountadmin_blocked(self, sql):
        with pytest.raises(ForbiddenSQLError, match="ACCOUNTADMIN"):
            guard_sql(sql)

    def test_single_quote_collapsed_by_normalization(self):
        assert normalize_sql("REVOKE ALL ON *.* FROM ROLE 'ACCOUNTADMIN'") == (
            "REVOKE ALL ON *.* FROM ROLE ACCOUNTADMIN"
        )

    def test_string_literal_normalized_when_identifier_shaped(self):
        # A literal whose body is identifier-shaped is indistinguishable from a
        # quoted identifier at this layer, so it is unquoted too. It still fails
        # to match any pattern that did not already match the unquoted form.
        assert normalize_sql("SELECT * FROM t WHERE name = 'ACCOUNTADMIN'") == (
            "SELECT * FROM t WHERE name = ACCOUNTADMIN"
        )

    def test_literal_with_spaces_keeps_its_quotes(self):
        # A body containing spaces cannot be an identifier, so the quotes stay
        # and the literal remains visible as data.
        assert normalize_sql("SELECT 'hello world'") == "SELECT 'hello world'"

    def test_string_literal_containing_accountadmin_is_allowed(self):
        guard_sql("SELECT * FROM t WHERE name = 'ACCOUNTADMIN'")

    def test_single_quoted_non_system_role_still_allowed(self):
        guard_sql("REVOKE ALL ON *.* FROM ROLE 'analyst'")


class TestNewlineBypass:
    """Root cause 2 — ``guard_sql`` searched without ``re.DOTALL``.

    ``.*`` never crossed a newline, so a statement split over several lines
    missed every pattern. ``is_destructive_sql`` and ``is_unscoped_mutation`` in
    the same module already used ``re.DOTALL``; the guard now matches them.
    """

    @pytest.mark.parametrize(
        "sql",
        [
            "REVOKE ALL ON *.*\nFROM ROLE\nACCOUNTADMIN",
            "REVOKE ALL ON *.* -- x\nFROM ROLE ACCOUNTADMIN",
            "REVOKE SELECT ON *.*\nFROM ROLE ACCOUNTADMIN",
            "REVOKE SELECT ON *.* -- x\nFROM ROLE ACCOUNTADMIN",
            "REVOKE ALL ON *.* FROM ROLE\nACCOUNTADMIN",
            "REVOKE ALL ON *.* FROM\nROLE ACCOUNTADMIN",
            "DROP ROLE\nACCOUNTADMIN",
            "DROP\nROLE\nACCOUNTADMIN",
            "\n\nDROP ROLE ACCOUNTADMIN\n\n",
        ],
    )
    def test_multiline_bypass_blocked(self, sql):
        with pytest.raises(ForbiddenSQLError, match="ACCOUNTADMIN"):
            guard_sql(sql)

    def test_crlf_line_endings_blocked(self):
        with pytest.raises(ForbiddenSQLError, match="ACCOUNTADMIN"):
            guard_sql("REVOKE ALL ON *.*\r\nFROM ROLE\r\nACCOUNTADMIN")

    def test_whitespace_run_squeezed_by_normalization(self):
        # Layout noise of every kind collapses to a single space, including a
        # tab after a newline.
        assert normalize_sql("REVOKE ALL ON *.*\n  FROM\n\tROLE\n   ACCOUNTADMIN") == (
            "REVOKE ALL ON *.* FROM ROLE ACCOUNTADMIN"
        )

    def test_newline_does_not_widen_a_legitimate_statement(self):
        guard_sql("REVOKE ALL ON *.*\nFROM ROLE\nanalyst")

    def test_quoted_identifier_after_layout_noise_still_collapsed(self):
        # The quoted-identifier rule is anchored on a whitespace run, so a tab
        # or a newline in front of the quote must not defeat it.
        with pytest.raises(ForbiddenSQLError, match="ACCOUNTADMIN"):
            guard_sql("REVOKE ALL ON *.* FROM ROLE\n\t'ACCOUNTADMIN'")


class TestIfExistsBypass:
    """Root cause 3 — three patterns lacked the ``(?:IF\\s+EXISTS\\s+)?`` group.

    ``DROP GLOBAL FUNCTION`` in the same list already carried it, so this was an
    oversight rather than a design decision. The engine accepts ``IF EXISTS`` on
    ``DROP ROLE`` (verified live), which makes the gap reachable.
    """

    @pytest.mark.parametrize(
        "sql",
        [
            "DROP ROLE IF EXISTS ACCOUNTADMIN",
            "DROP ROLE IF EXISTS `ACCOUNTADMIN`",
            "DROP ROLE IF EXISTS 'ACCOUNTADMIN'",
            'DROP ROLE IF EXISTS "ACCOUNTADMIN"',
            "drop role if exists accountadmin",
            "DROP ROLE\nIF\nEXISTS\nACCOUNTADMIN",
            "ALTER ROLE IF EXISTS ACCOUNTADMIN RENAME TO evil",
            "ALTER ROLE IF EXISTS ACCOUNTADMIN",
            "ALTER ROLE IF EXISTS evil RENAME TO ACCOUNTADMIN",
        ],
    )
    def test_if_exists_accountadmin_blocked(self, sql):
        with pytest.raises(ForbiddenSQLError, match="ACCOUNTADMIN"):
            guard_sql(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            "DROP GLOBAL FUNCTION IF EXISTS AI_COMPLETE(STRING)",
            "DROP GLOBAL FUNCTION IF EXISTS ML_PREDICT(VARCHAR)",
        ],
    )
    def test_builtin_udf_if_exists_still_blocked(self, sql):
        with pytest.raises(ForbiddenSQLError, match="built-in function"):
            guard_sql(sql)

    def test_if_exists_on_ordinary_role_allowed(self):
        # Over-blocking check: the clause itself is legitimate.
        guard_sql("DROP ROLE IF EXISTS analyst")


class TestRevokeIfExistsBypass:
    """NOVA-19 — the ``REVOKE`` patterns were missed by the ``IF EXISTS`` fix.

    ``REVOKE`` joins its target role with ``ROLE`` (or directly), so the group
    has to sit after ``FROM ROLE`` — and after the bare ``FROM`` form — the same
    way it sits after ``DROP ROLE`` and ``ALTER ROLE``. Without it the two
    keywords had to be adjacent and a single ``IF EXISTS`` clause walked the
    statement past the guard. All of these were allowed on ``7b03cd9``.
    """

    @pytest.mark.parametrize(
        "sql",
        [
            "REVOKE ALL ON *.* FROM ROLE IF EXISTS ACCOUNTADMIN",
            "REVOKE ALL ON *.* FROM ROLE IF EXISTS `ACCOUNTADMIN`",
            "REVOKE ALL ON *.* FROM ROLE IF EXISTS 'ACCOUNTADMIN'",
            'REVOKE ALL ON *.* FROM ROLE IF EXISTS "ACCOUNTADMIN"',
            "REVOKE ALL ON *.* FROM ROLE IF EXISTS [ACCOUNTADMIN]",
            "REVOKE ALL ON *.* FROM IF EXISTS ACCOUNTADMIN",
            "REVOKE USAGE ON *.* FROM ROLE IF EXISTS ACCOUNTADMIN",
            "REVOKE SELECT ON *.* FROM IF EXISTS ACCOUNTADMIN",
            "revoke all on *.* from role if exists accountadmin",
            "REVOKE ALL ON *.* FROM ROLE\nIF\nEXISTS\nACCOUNTADMIN",
            "REVOKE ALL /*c*/ ON *.* FROM ROLE IF EXISTS\n'ACCOUNTADMIN'",
            "REVOKE ALL ON *.* FROM ROLE IF /*c*/ EXISTS ACCOUNTADMIN",
        ],
    )
    def test_revoke_if_exists_accountadmin_blocked(self, sql):
        with pytest.raises(ForbiddenSQLError, match="ACCOUNTADMIN"):
            guard_sql(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            "REVOKE ALL ON *.* FROM ROLE IF EXISTS analyst",
            "REVOKE ALL ON *.* FROM IF EXISTS analyst",
            "REVOKE ALL ON *.* FROM ROLE IF EXISTS 'analyst'",
            "REVOKE USAGE ON *.* FROM ROLE IF EXISTS analyst",
        ],
    )
    def test_revoke_if_exists_on_ordinary_role_allowed(self, sql):
        # Over-blocking check: ``IF EXISTS`` on a non-system role is legitimate
        # and must stay executable.
        guard_sql(sql)

    def test_revoke_if_exists_does_not_leak_across_statement_boundary(self):
        # The privilege span is still bounded by `[^;]*?`, so adding the group
        # did not let a REVOKE naming a safe role reach a later ACCOUNTADMIN.
        guard_sql("REVOKE ALL ON *.* FROM ROLE IF EXISTS analyst; SELECT 1")


class TestBypassComposition:
    """The three bypasses compose; fixing one must not re-open another."""

    @pytest.mark.parametrize(
        "sql",
        [
            "REVOKE ALL /*c*/ ON *.* FROM ROLE\n'ACCOUNTADMIN'",
            "DROP /*c*/ ROLE\nIF EXISTS\n'ACCOUNTADMIN'",
            'DROP ROLE IF EXISTS\n"ACCOUNTADMIN"',
            "REVOKE ALL ON *.* -- c\nFROM ROLE\n'ACCOUNTADMIN'",
        ],
    )
    def test_combined_variants_blocked(self, sql):
        with pytest.raises(ForbiddenSQLError, match="ACCOUNTADMIN"):
            guard_sql(sql)


class TestEdgeCases:
    """Inputs the issue asked to be probed, recorded with their real behaviour.

    ``\\b`` treats a backslash as a normal character, so ``AC\\COUNTADMIN`` is a
    *different* identifier to both the guard and StarRocks, not a spelling of
    ``ACCOUNTADMIN``. Both sides agree, and no engine accepts a backslash inside
    a bare role name, so this is not a reachable bypass.
    """

    @pytest.mark.parametrize(
        "sql",
        [
            "DROP ROLE AC\\COUNTADMIN",
            "REVOKE ALL ON *.* FROM ROLE AC\\COUNTADMIN",
        ],
    )
    def test_backslash_identifier_is_a_different_role(self, sql):
        guard_sql(sql)

    def test_unicode_lookalike_roles_are_still_allowed(self):
        # U+212A KELVIN SIGN and U+017F LONG S do fold to ASCII 'k'/'s' under
        # `re.IGNORECASE`, but no pattern in this module looks at those letters,
        # so the fold cannot produce a match here. Pinned so that a future
        # pattern containing K/S cannot start over-blocking unnoticed.
        guard_sql("DROP ROLE \u212aELVIN")
        guard_sql("REVOKE ALL ON *.* FROM ROLE \u212aELVIN")
        guard_sql("DROP ROLE \u017frank")

    def test_unicode_accountadmin_lookalike_is_allowed(self):
        # Cyrillic А (U+0410) in place of ASCII A. StarRocks reads it as a
        # distinct role, and it does not fold to ASCII 'A' either, so neither
        # side treats it as ACCOUNTADMIN. Allowing it is correct, not a gap.
        guard_sql("DROP ROLE \u0410CCOUNTADMIN")


class TestGuaranteesPreserved:
    """Controls that were already blocked must stay blocked, and legitimate
    statements — including ones targeting a non-system role — must stay
    executable."""

    @pytest.mark.parametrize(
        "sql",
        [
            "DROP ROLE ACCOUNTADMIN",
            "REVOKE ALL ON *.* FROM ROLE ACCOUNTADMIN",
            "REVOKE USAGE ON *.* FROM ROLE ACCOUNTADMIN",
            "REVOKE ALL ON *.* FROM ACCOUNTADMIN",
            "ALTER ROLE ACCOUNTADMIN SET DEFAULT ROLE none",
            "ALTER ROLE evil RENAME TO ACCOUNTADMIN",
            "DROP /*x*/ ROLE `ACCOUNTADMIN`",
            'REVOKE ALL ON *.* FROM ROLE "ACCOUNTADMIN"',
            "REVOKE ALL ON *.* FROM ROLE `ACCOUNTADMIN`",
            "DROP USER root",
            "DROP GLOBAL FUNCTION AI_COMPLETE(STRING)",
        ],
    )
    def test_still_blocked(self, sql):
        with pytest.raises(ForbiddenSQLError):
            guard_sql(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 1",
            "SELECT * FROM my_table WHERE id = 1",
            "CREATE ROLE analyst",
            "DROP ROLE analyst",
            "DROP ROLE IF EXISTS analyst",
            "REVOKE ALL ON *.* FROM ROLE analyst",
            "REVOKE ALL ON *.* FROM ROLE `analyst`",
            "REVOKE ALL ON *.* FROM ROLE 'analyst'",
            "REVOKE ALL ON *.*\nFROM ROLE\nanalyst",
            "GRANT SELECT ON db.t TO ROLE analyst",
            "ALTER ROLE analyst RENAME TO analyst2",
            "DROP USER testuser",
            "CREATE TABLE test (id INT)",
            "",
            "   \n\t ",
        ],
    )
    def test_still_allowed(self, sql):
        guard_sql(sql)

    def test_revoke_does_not_leak_across_statement_boundary(self):
        # The privilege span is bounded by `[^;]*?`, so a REVOKE naming a safe
        # role cannot be made to match a later ACCOUNTADMIN in the same script.
        # ``guard_sql`` now anchors per statement as well, so the tail is checked
        # on its own merits rather than being invisible — and being a clean
        # literal it is still allowed. See
        # ``test_defense_in_depth_hardening.TestT1OverBlockingControls`` for the
        # mirror case, where the tail is a real forbidden statement.
        guard_sql("REVOKE ALL ON *.* FROM ROLE analyst; SELECT 'ACCOUNTADMIN'")
