"""Regression tests for the defense-in-depth hardening of NOVA-13.

Three findings from the formal QA verdict on ``e66eecd`` (PR #4). None was an
exploitable hole at the time; each was a latent bypass that a plausible-looking
refactor would have turned live.

* **T-1** — ``guard_sql`` had no statement anchor, so its strength depended on
  the caller splitting SQL first. Eight call sites hand it a raw blob.
* **T-2** — ``_protect_role`` compared the raw string, so ``'ACCOUNTADMIN'`` and
  `` `ACCOUNTADMIN` `` walked past it while ``guard_sql`` blocked both.
* **T-3** — ``_build_privilege_sql`` always emitted ``TO ROLE``, which is
  invalid on ``REVOKE``, and ``service.py`` patched it up with a string
  ``.replace()`` that hid the wrong preposition.

Unit-level: no engine, no network. The last class in each section pins the
guarantees that must survive — over-blocking is a regression, not safety.
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


class TestT2ProtectRoleNormalization:
    """``_protect_role`` must use the guard's own definition of role identity.

    StarRocks accepts ``ACCOUNTADMIN``, ``'ACCOUNTADMIN'``, ``"ACCOUNTADMIN"``
    and `` `ACCOUNTADMIN` `` as the same role. The old check compared
    ``role_name.upper()`` against ``PROTECTED_ROLES``, so every quoted spelling
    passed — while ``guard_sql`` blocked all of them. Two controls in one system
    disagreeing about identity is how one bypass becomes two.
    """

    @pytest.fixture
    def svc(self) -> UserService:
        return UserService()

    @pytest.mark.parametrize(
        "role",
        [
            "ACCOUNTADMIN",
            "accountadmin",
            "AccountAdmin",
            "'ACCOUNTADMIN'",
            "`ACCOUNTADMIN`",
            '"ACCOUNTADMIN"',
            "[ACCOUNTADMIN]",
            "'accountadmin'",
            "`accountadmin`",
            "  ACCOUNTADMIN  ",
            "\t'ACCOUNTADMIN'\n",
            "  'ACCOUNTADMIN'  ",
        ],
    )
    def test_quoted_and_cased_spellings_are_rejected(self, svc, role):
        with pytest.raises(PermissionError, match="protected role"):
            svc._protect_role(role)

    @pytest.mark.parametrize(
        "role",
        [
            "analyst",
            "'analyst'",
            "`analyst`",
            '"analyst"',
            "db_admin",
            "cluster_admin",
            "user_admin",
            "security_admin",
            "public",
            "ANALYST",
            "ACCOUNT_ADMIN",
            "",
        ],
    )
    def test_ordinary_roles_are_not_protected(self, svc, role):
        # `db_admin`/`public` are built-ins but not protected: they are mutable
        # by design, and _protect_role is the immutability check.
        svc._protect_role(role)

    def test_protect_role_agrees_with_guard_sql_on_every_spelling(self, svc):
        """The two controls must not drift apart again.

        For each spelling, ask the guard whether it is ACCOUNTADMIN in a REVOKE
        and ask ``_protect_role`` whether it may be modified. They must agree.
        """
        spellings = [
            "ACCOUNTADMIN",
            "accountadmin",
            "'ACCOUNTADMIN'",
            "`ACCOUNTADMIN`",
            '"ACCOUNTADMIN"',
            "[ACCOUNTADMIN]",
            "analyst",
            "'analyst'",
            "`analyst`",
        ]
        for spelling in spellings:
            guard_rejects = False
            try:
                guard_sql(f"REVOKE ALL ON *.* FROM ROLE {spelling}")
            except ForbiddenSQLError:
                guard_rejects = True

            service_rejects = False
            try:
                svc._protect_role(spelling)
            except PermissionError:
                service_rejects = True

            assert guard_rejects == service_rejects, (
                f"guard_sql and _protect_role disagree about {spelling!r}: "
                f"guard_blocks={guard_rejects}, protect_role_blocks={service_rejects}"
            )

    def test_space_padded_literal_is_a_different_identifier_on_both_sides(self, svc):
        """``' ACCOUNTADMIN '`` is *not* ACCOUNTADMIN, and both sides agree.

        The body contains spaces, so it cannot be an identifier and the quotes
        stay — ``guard_sql`` allows it for the same reason ``_protect_role``
        does. Pinned so that "harden this further" cannot quietly make the two
        controls disagree in the other direction.
        """
        guard_sql("REVOKE ALL ON *.* FROM ROLE ' ACCOUNTADMIN '")
        guard_sql("DROP ROLE ' ACCOUNTADMIN '")
        svc._protect_role("' ACCOUNTADMIN '")

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


# ── T-3: privilege builder ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("scope", "selector_mode", "kwargs"),
    [
        ("SYSTEM", "system", {}),
        ("CATALOG", "catalog", {"catalog": "cat1"}),
        ("DATABASE", "all_databases", {}),
        ("DATABASE", "single_db", {"database": "db1"}),
        ("TABLE", "all_databases", {}),
        ("TABLE", "all_in_database", {"database": "db1"}),
        ("TABLE", "object", {"database": "db1", "object_name": "t1"}),
        ("VIEW", "all_databases", {}),
        ("VIEW", "all_in_database", {"database": "db1"}),
        ("VIEW", "object", {"database": "db1", "object_name": "v1"}),
        ("MATERIALIZED VIEW", "all_databases", {}),
        ("MATERIALIZED VIEW", "all_in_database", {"database": "db1"}),
        ("MATERIALIZED VIEW", "object", {"database": "db1", "object_name": "mv1"}),
    ],
)
class TestT3PrivilegeSqlPreposition:
    """The builder picks the preposition; no caller patches it afterwards.

    ``GRANT`` attaches the grantee with ``TO ROLE``; ``REVOKE`` detaches it with
    ``FROM ROLE``. ``TO ROLE`` on a REVOKE is a syntax error, so the old builder
    — which always emitted ``TO ROLE`` — relied on
    ``service.py:747``'s ``sql.replace(" TO ROLE ", " FROM ROLE ")`` to produce
    valid SQL. That replace is what hid the wrong preposition.
    """

    @pytest.fixture
    def svc(self) -> UserService:
        return UserService()

    def test_revoke_branch_uses_from_role_and_never_to_role(
        self, svc, scope, selector_mode, kwargs
    ):
        sql = svc._build_privilege_sql(
            "REVOKE", "analyst", _any_privilege(scope), scope, selector_mode, **kwargs
        )
        assert "FROM ROLE `analyst`" in sql, sql
        assert "TO ROLE" not in sql, sql

    def test_grant_branch_still_uses_to_role(self, svc, scope, selector_mode, kwargs):
        sql = svc._build_privilege_sql(
            "GRANT", "analyst", _any_privilege(scope), scope, selector_mode, **kwargs
        )
        assert "TO ROLE `analyst`" in sql, sql
        assert "FROM ROLE" not in sql, sql

    def test_preposition_is_the_only_difference_between_grant_and_revoke(
        self, svc, scope, selector_mode, kwargs
    ):
        # The two must not diverge in any other respect: same privilege, same
        # object path, same role — only the preposition and the leading keyword.
        privilege = _any_privilege(scope)
        granted = svc._build_privilege_sql(
            "GRANT", "analyst", privilege, scope, selector_mode, **kwargs
        )
        revoked = svc._build_privilege_sql(
            "REVOKE", "analyst", privilege, scope, selector_mode, **kwargs
        )
        assert granted.replace("GRANT", "X", 1).replace(" TO ROLE ", " / ") == revoked.replace(
            "REVOKE", "X", 1
        ).replace(" FROM ROLE ", " / "), f"{granted!r} vs {revoked!r}"


class TestT3PrivilegeSqlBehaviourPreserved:
    """Everything the builder produced before, minus the wrong preposition."""

    @pytest.fixture
    def svc(self) -> UserService:
        return UserService()

    @pytest.mark.parametrize(
        ("privilege", "scope", "selector_mode", "kwargs"),
        [
            ("SELECT", "TABLE", "all_databases", {}),
            ("OPERATE", "SYSTEM", "system", {}),
            ("USAGE", "CATALOG", "catalog", {"catalog": "cat1"}),
            ("ALTER", "DATABASE", "single_db", {"database": "db1"}),
            ("ALTER", "DATABASE", "all_databases", {}),
            ("SELECT", "TABLE", "object", {"database": "db1", "object_name": "t1"}),
            ("SELECT", "TABLE", "all_in_database", {"database": "db1"}),
            ("SELECT", "VIEW", "all_in_database", {"database": "db1"}),
            ("SELECT", "MATERIALIZED VIEW", "all_databases", {}),
        ],
    )
    def test_revoke_output_equals_the_old_replaced_output(
        self, svc, privilege, scope, selector_mode, kwargs
    ):
        """Removing the ``.replace()`` must not change one character of the SQL.

        Reproduces the old two-step pipeline — build with ``TO ROLE``, then
        string-replace — and asserts the new single-step output is identical.
        """
        built = svc._build_privilege_sql(
            "REVOKE", "analyst", privilege, scope, selector_mode, **kwargs
        )
        old_output = built.replace("FROM ROLE", "TO ROLE").replace(
            " TO ROLE ", " FROM ROLE "
        )
        assert built == old_output

    def test_grant_with_grant_option_suffix_preserved(self, svc):
        sql = svc._build_privilege_sql(
            "GRANT",
            "analyst",
            "SELECT",
            "TABLE",
            "object",
            database="db1",
            object_name="t1",
            with_grant_option=True,
        )
        assert sql == "GRANT SELECT ON TABLE `db1`.`t1` TO ROLE `analyst` WITH GRANT OPTION"

    def test_grant_option_is_not_appended_to_revoke(self, svc):
        sql = svc._build_privilege_sql(
            "REVOKE", "analyst", "SELECT", "TABLE", "object", database="db1", object_name="t1"
        )
        assert "WITH GRANT OPTION" not in sql

    def test_role_clause_rejects_an_unknown_action(self, svc):
        # A future third action must not silently inherit the GRANT wording.
        with pytest.raises(ValueError, match="Unsupported privilege action"):
            svc._role_clause("DENY", "`analyst`")

    def test_revoke_privilege_does_not_post_process_the_sql(self, svc, monkeypatch):
        """``revoke_privilege`` must send the builder's output verbatim.

        Guards against the ``.replace()`` being reintroduced anywhere between
        the builder and the engine.
        """
        captured: list[str] = []

        async def fake_execute_system(sql: str):
            captured.append(sql)
            return {"columns": [], "rows": []}

        monkeypatch.setattr("app.modules.users.service.db.execute_system", fake_execute_system)

        import asyncio

        returned = asyncio.run(svc.revoke_privilege("analyst", "SELECT", "TABLE", "all_databases"))
        assert captured == [returned]
        assert "FROM ROLE `analyst`" in returned
        assert "TO ROLE" not in returned

    def test_grant_privilege_does_not_post_process_the_sql(self, svc, monkeypatch):
        captured: list[str] = []

        async def fake_execute_system(sql: str):
            captured.append(sql)
            return {"columns": [], "rows": []}

        monkeypatch.setattr("app.modules.users.service.db.execute_system", fake_execute_system)

        import asyncio

        returned = asyncio.run(svc.grant_privilege("analyst", "SELECT", "TABLE", "all_databases"))
        assert captured == [returned]
        assert "TO ROLE `analyst`" in returned


def _any_privilege(scope: str) -> str:
    """Return a privilege the given scope accepts, for parametrized use."""
    return {
        "SYSTEM": "OPERATE",
        "CATALOG": "USAGE",
        "DATABASE": "ALTER",
        "TABLE": "SELECT",
        "VIEW": "SELECT",
        "MATERIALIZED VIEW": "SELECT",
    }[scope.upper()]
