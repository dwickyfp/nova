"""Unit tests for proxy session state and statement routing.

The rules pinned here exist because the proxy's statement handling is where it
diverges from the HTTP path, and every divergence is a chance to send the
engine something the dialect pipeline will misread.

The one that motivates the whole module: ``parse_sql`` treats ``@name`` as a
stage reference (``backend/app/modules/query/dialect/parser.py:51``), so a
``SET @x = 1`` forwarded to ``QueryService`` fails with ``Stage 'x' not found``
and ``SET @my_stage = 1`` is silently rewritten into a ``FILES()`` call. ``SET``
therefore never reaches the engine.
"""

import pytest

from app.proxy.session import (
    HIDDEN_DATABASES,
    SessionState,
    handle_set_statement,
    is_show_databases,
    parse_use_statement,
    split_statements,
)


class TestSplitStatements:
    def test_splits_on_semicolons(self):
        assert split_statements("SELECT 1; SELECT 2") == ["SELECT 1", "SELECT 2"]

    def test_drops_empty_statements(self):
        assert split_statements("SELECT 1;;; SELECT 2;") == ["SELECT 1", "SELECT 2"]

    def test_semicolon_inside_a_literal_is_text(self):
        assert split_statements("SELECT 'a;b'") == ["SELECT 'a;b'"]

    def test_doubled_quote_escape_keeps_the_literal_open(self):
        assert split_statements("SELECT 'it''s; fine'") == ["SELECT 'it''s; fine'"]

    def test_line_comment_hides_a_semicolon(self):
        assert split_statements("SELECT 1 -- ; not a boundary\n; SELECT 2") == [
            "SELECT 1 -- ; not a boundary",
            "SELECT 2",
        ]

    def test_block_comment_is_one_statement(self):
        assert split_statements("SELECT /* ; */ 42") == ["SELECT /* ; */ 42"]

    def test_unterminated_block_comment_swallows_the_rest(self):
        assert split_statements("SELECT 1 /* ; SELECT 2") == ["SELECT 1 /* ; SELECT 2"]

    def test_trailing_statement_without_semicolon(self):
        assert split_statements("SELECT 1; SELECT 2") == ["SELECT 1", "SELECT 2"]

    def test_empty_input(self):
        assert split_statements("   ") == []


class TestSetStatements:
    """``SET`` is session state and must never reach the engine."""

    def test_user_variable_assignment_is_tracked(self):
        session = SessionState()
        result = handle_set_statement("SET @x = 1", session)
        assert result.handled is True
        assert result.error is None
        assert session.user_variables["x"] == "1"

    def test_stage_shaped_variable_is_tracked_not_translated(self):
        """``@my_stage`` is the trap: the parser would rewrite it as FILES()."""
        session = SessionState()
        result = handle_set_statement("SET @my_stage = 1", session)
        assert result.handled is True
        assert session.user_variables["my_stage"] == "1"

    def test_walrus_assignment_operator(self):
        session = SessionState()
        assert handle_set_statement("SET @x := 'abc'", session).handled is True
        assert session.user_variables["x"] == "abc"

    def test_single_quotes_are_stripped(self):
        session = SessionState()
        handle_set_statement("SET @name = 'Andi'", session)
        assert session.user_variables["name"] == "Andi"

    def test_escaped_quote_inside_a_literal(self):
        session = SessionState()
        handle_set_statement("SET @name = 'it''s'", session)
        assert session.user_variables["name"] == "it's"

    def test_numeric_value_kept_verbatim(self):
        session = SessionState()
        handle_set_statement("SET @n = 3.14", session)
        assert session.user_variables["n"] == "3.14"

    def test_assignment_is_case_insensitive_on_the_variable_name(self):
        session = SessionState()
        handle_set_statement("SET @Foo = 1", session)
        assert session.user_variables["foo"] == "1"

    def test_trailing_semicolon_does_not_leak_into_the_value(self):
        session = SessionState()
        handle_set_statement("SET @x = 1;", session)
        assert session.user_variables["x"] == "1"

    def test_set_role_updates_active_role(self):
        session = SessionState()
        result = handle_set_statement("SET ROLE ACCOUNTADMIN", session)
        assert result.handled is True
        assert session.active_role == "ACCOUNTADMIN"

    def test_set_role_quoted(self):
        session = SessionState()
        handle_set_statement("SET ROLE 'analyst'", session)
        assert session.active_role == "analyst"

    def test_set_role_is_case_insensitive(self):
        session = SessionState()
        assert handle_set_statement("set role accountadmin", session).handled is True
        assert session.active_role == "accountadmin"

    def test_set_names_is_handled_without_state(self):
        session = SessionState()
        result = handle_set_statement("SET NAMES utf8mb4", session)
        assert result.handled is True
        assert session.user_variables == {}

    def test_set_character_set_alias(self):
        session = SessionState()
        assert handle_set_statement("SET CHARACTER SET utf8", session).handled is True

    @pytest.mark.parametrize(
        "statement",
        [
            "SET autocommit = 1",
            "SET sql_mode = 'STRICT_ALL_TABLES'",
            "SET time_zone = '+07:00'",
            "SET foreign_key_checks = 0",
            "BEGIN",
            "START TRANSACTION",
            "COMMIT",
            "ROLLBACK",
        ],
    )
    def test_transaction_and_noop_family_is_consumed(self, statement):
        session = SessionState()
        result = handle_set_statement(statement, session)
        assert result.handled is True
        assert result.error is None

    def test_set_global_is_refused_not_ignored(self):
        """A client that thinks it changed a global and did not is worse off.

        ``SET @@global.x`` cannot be honoured per connection, so it is reported
        as an error rather than answered OK.
        """
        session = SessionState()
        result = handle_set_statement("SET @@global.x = 1", session)
        assert result.handled is False
        assert result.error is not None
        assert "GLOBAL" in result.error

    def test_unrecognised_set_falls_through_to_the_engine(self):
        """Unknown session syntax must not be silently swallowed."""
        session = SessionState()
        result = handle_set_statement("SET SOMETHING NOVA DOES NOT KNOW", session)
        assert result.handled is False
        assert result.error is None

    def test_non_set_statement_is_not_handled(self):
        session = SessionState()
        assert handle_set_statement("SELECT 1", session).handled is False

    def test_multiple_assignments_accumulate(self):
        session = SessionState()
        handle_set_statement("SET @a = 1", session)
        handle_set_statement("SET @b = 2", session)
        assert session.user_variables == {"a": "1", "b": "2"}

    def test_reassignment_overwrites(self):
        session = SessionState()
        handle_set_statement("SET @a = 1", session)
        handle_set_statement("SET @a = 2", session)
        assert session.user_variables["a"] == "2"


class TestUseStatement:
    def test_plain_use(self):
        assert parse_use_statement("USE NOVA_DEMO") == "NOVA_DEMO"

    def test_backticked(self):
        assert parse_use_statement("USE `NOVA_DEMO`") == "NOVA_DEMO"

    def test_qualified_use_keeps_the_first_segment(self):
        """``USE DATALAKE.bronze`` is what the design doc's UI example writes.

        The engine resolves the qualified form; the proxy tracks the database
        context that later statements are scoped to.
        """
        assert parse_use_statement("USE DATALAKE.bronze") == "DATALAKE"

    def test_case_insensitive_keyword(self):
        assert parse_use_statement("use nova_demo") == "nova_demo"

    def test_trailing_semicolon(self):
        assert parse_use_statement("USE NOVA_DEMO;") == "NOVA_DEMO"

    def test_not_a_use_statement(self):
        assert parse_use_statement("SELECT 1") is None
        assert parse_use_statement("USE") is None
        assert parse_use_statement("") is None

    def test_use_updates_session_database(self):
        session = SessionState()
        session.set_database("NOVA_DEMO")
        assert session.database == "NOVA_DEMO"

    def test_empty_database_clears_the_context(self):
        session = SessionState()
        session.set_database("NOVA_DEMO")
        session.set_database("")
        assert session.database is None


class TestShowDatabasesDetection:
    @pytest.mark.parametrize(
        "statement",
        [
            "SHOW DATABASES",
            "show databases",
            "SHOW DATABASES;",
            "  SHOW  DATABASES  ",
            "SHOW SCHEMAS",
        ],
    )
    def test_recognised(self, statement):
        assert is_show_databases(statement) is True

    @pytest.mark.parametrize(
        "statement",
        ["SHOW TABLES", "SHOW DATABASES LIKE 'N%'", "SELECT 1", "SHOW CREATE DATABASE x"],
    )
    def test_not_recognised(self, statement):
        assert is_show_databases(statement) is False

    def test_nova_system_is_hidden(self):
        assert "NOVA_SYSTEM" in HIDDEN_DATABASES

    def test_engine_internals_are_hidden(self):
        assert {"information_schema", "sys", "_statistics_"} <= HIDDEN_DATABASES
