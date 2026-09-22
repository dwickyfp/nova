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
    parse_role_statement,
    parse_use_statement,
    split_statements,
    substitute_user_variables,
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
        assert session.user_variables["x"] == "'abc'"

    def test_string_value_keeps_its_quotes(self):
        """The quotes are part of the value, not decoration.

        Substituting the value into a later statement splices this text in
        verbatim, so dropping the quotes would turn ``SET @name = 'Andi'``
        followed by ``SELECT @name`` into ``SELECT Andi`` — a bare identifier,
        i.e. an unknown column. Storing the literal form is what makes the
        round trip work.
        """
        session = SessionState()
        handle_set_statement("SET @name = 'Andi'", session)
        assert session.user_variables["name"] == "'Andi'"

    def test_escaped_quote_inside_a_literal(self):
        session = SessionState()
        handle_set_statement("SET @name = 'it''s'", session)
        assert session.user_variables["name"] == "'it''s'"

    def test_double_quoted_value_is_rewritten_to_single_quotes(self):
        """Which quote character delimits a string is a SQL-mode detail.

        Rewriting the double-quoted form to the single-quoted one makes the
        stored text unambiguous regardless of the engine's mode.
        """
        session = SessionState()
        handle_set_statement('SET @name = "Andi"', session)
        assert session.user_variables["name"] == "'Andi'"

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

    @pytest.mark.parametrize(
        ("statement", "expected"),
        [
            ("SET ROLE ACCOUNTADMIN", "ACCOUNTADMIN"),
            ("SET ROLE 'analyst'", "analyst"),
            ("set role accountadmin", "accountadmin"),
            ("USE ROLE finance", "finance"),
            ("SET ROLE DEFAULT", "DEFAULT"),
        ],
    )
    def test_role_statement_requires_central_activation(self, statement, expected):
        session = SessionState(active_role="marketing")
        result = handle_set_statement(statement, session)

        assert result.handled is False
        if statement.upper().startswith("SET ROLE"):
            assert result.error == "SET ROLE must be validated by the role activation service"
        else:
            assert result.error is None
        assert session.active_role == "marketing"
        assert parse_role_statement(statement) == expected

    @pytest.mark.parametrize(
        "statement",
        ["SET ROLE ALL", "SET ROLE NONE", "SET ROLE marketing, finance"],
    )
    def test_unsafe_role_activation_is_rejected_without_state_change(self, statement):
        session = SessionState(active_role="marketing")

        with pytest.raises(ValueError, match="Exactly one named role"):
            parse_role_statement(statement)

        assert session.active_role == "marketing"

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


class TestUserVariableSubstitution:
    """The read half of ``SET @x = …`` — NOVA-25.

    The write path alone was not a feature: ``SET @x`` was accepted and stored,
    but ``SELECT @x`` went to the engine untouched, where Nova's ``@stage``
    pattern claimed it as a stage named ``x`` and failed every read with
    ``Stage 'x' not found``. These tests pin the substitution that closes it, and
    the tokenizer rules that keep it from touching text that merely looks like a
    reference.
    """

    @staticmethod
    def _session(**values: str) -> SessionState:
        session = SessionState()
        session.user_variables = dict(values)
        return session

    def test_string_variable_substitutes_into_a_select(self):
        session = self._session(x="'QA_MARKER_12345'")
        result = substitute_user_variables("SELECT @x AS val", session)
        assert result.sql == "SELECT 'QA_MARKER_12345' AS val"
        assert result.substituted == ["x"]

    def test_numeric_variable_substitutes_as_a_number(self):
        """``@n`` must splice in unquoted, or ``1 + '5'`` would become a string."""
        session = self._session(n="5")
        result = substitute_user_variables("SELECT 1 + @n", session)
        assert result.sql == "SELECT 1 + 5"

    def test_reference_in_a_where_clause(self):
        session = self._session(threshold="100")
        result = substitute_user_variables("SELECT * FROM t WHERE amount > @threshold", session)
        assert result.sql == "SELECT * FROM t WHERE amount > 100"

    def test_multiple_references_substitute(self):
        session = self._session(a="1", b="2")
        result = substitute_user_variables("SELECT @a, @b, @a", session)
        assert result.sql == "SELECT 1, 2, 1"
        assert result.substituted == ["a", "b", "a"]

    def test_name_lookup_is_case_insensitive(self):
        session = self._session(x="1")
        assert substitute_user_variables("SELECT @X", session).sql == "SELECT 1"

    def test_unset_variable_is_left_for_the_engine(self):
        """An unknown name is not the proxy's to invent a value for.

        Substituting ``NULL`` would silently change query semantics, and
        reporting an error would pre-empt the engine's own diagnosis. The
        reference is left intact and reported to the caller, and — because a
        bare ``@name`` is no longer a stage reference — the engine answers with
        its own "unknown user variable" behaviour rather than a stage error.
        """
        session = self._session(x="1")
        result = substitute_user_variables("SELECT @unset", session)
        assert result.sql == "SELECT @unset"
        assert result.substituted == []
        assert result.unknown == ["unset"]

    def test_reference_inside_a_string_literal_is_not_replaced(self):
        """``'@x'`` is data. Replacing it would corrupt the client's text."""
        session = self._session(x="'injected'")
        result = substitute_user_variables("SELECT '@x' AS literal", session)
        assert result.sql == "SELECT '@x' AS literal"
        assert result.substituted == []

    def test_reference_inside_a_line_comment_is_not_replaced(self):
        session = self._session(x="1")
        result = substitute_user_variables("SELECT 1 -- @x\n", session)
        assert result.sql == "SELECT 1 -- @x\n"
        assert result.substituted == []

    def test_reference_inside_a_block_comment_is_not_replaced(self):
        session = self._session(x="1")
        result = substitute_user_variables("SELECT /* @x */ 2", session)
        assert result.sql == "SELECT /* @x */ 2"
        assert result.substituted == []

    def test_reference_inside_a_backquoted_identifier_is_not_replaced(self):
        session = self._session(x="1")
        result = substitute_user_variables("SELECT `@x` FROM t", session)
        assert result.sql == "SELECT `@x` FROM t"
        assert result.substituted == []

    def test_system_variable_is_never_substituted(self):
        """``@@x`` is the engine's, even when a session variable shares the name."""
        session = self._session(version_comment="'spoofed'")
        result = substitute_user_variables("SELECT @@version_comment", session)
        assert result.sql == "SELECT @@version_comment"
        assert result.substituted == []

    def test_stage_reference_is_left_alone(self):
        """A dotted reference belongs to the dialect engine.

        ``@products.products_new.csv`` is a stage even when a session variable
        called ``products`` exists; the stage interpretation is what the engine
        would otherwise have acted on, and resolving the clash in the client's
        favour would make a stored value silently shadow a stage.
        """
        session = self._session(products="'shadow'")
        result = substitute_user_variables("SELECT * FROM @products.products_new.csv", session)
        assert result.sql == "SELECT * FROM @products.products_new.csv"
        assert result.substituted == []

    def test_name_clash_dotted_stage_still_wins(self):
        """The stage wins over a session variable of the same name.

        ``session.py``'s contract says the ambiguity is the client's and the
        stage wins. Reading the stored value first turned ``@stage1.data.csv``
        into the variable's text and left the engine with a path it could not
        resolve.
        """
        session = self._session(stage1="'CSV_FILE'")
        result = substitute_user_variables("SELECT * FROM @stage1.data.csv", session)
        assert result.sql == "SELECT * FROM @stage1.data.csv"
        assert result.substituted == []

    def test_name_clash_does_not_substitute_into_a_dotted_reference(self):
        """The frame of the QA reproduction: a dot after the name.

        ``@stage1.data.csv`` is a stage path, not ``@stage1`` followed by text,
        so the stored value must not be spliced in even when the name matches.
        """
        session = self._session(stage1="'CSV_FILE'")
        result = substitute_user_variables("SELECT * FROM @stage1.data.csv", session)
        assert result.substituted == []
        assert "CSV_FILE" not in result.sql

    def test_bare_name_in_from_keeps_the_stage(self):
        """``SELECT * FROM @stage1`` is a stage once the variable shares its name.

        This is the case the reorder fixes. ``parser.py`` classifies ``@name``
        by context, and after ``FROM`` a bare ``@stage1`` is a stage — so
        substituting the variable here would send ``SELECT * FROM 'CSV_FILE'``
        and lose the stage entirely.
        """
        session = self._session(stage1="'CSV_FILE'")
        result = substitute_user_variables("SELECT * FROM @stage1", session)
        assert result.sql == "SELECT * FROM @stage1"
        assert result.substituted == []

    def test_bare_name_in_list_files_keeps_the_stage(self):
        """``LIST FILES @stage1`` is a stage once the variable shares its name."""
        session = self._session(stage1="'CSV_FILE'")
        result = substitute_user_variables("LIST FILES @stage1", session)
        assert result.sql == "LIST FILES @stage1"
        assert result.substituted == []

    def test_set_then_select_from_stage_keeps_the_stage(self):
        """End to end: the exact reproduction from the issue.

        ``SET @stage1 = 1`` stores ``1`` under ``stage1``; the following
        ``SELECT * FROM @stage1`` is a stage and must not pick the value up.
        """
        session = SessionState()
        handle_set_statement("SET @stage1 = 1", session)
        result = substitute_user_variables("SELECT * FROM @stage1", session)
        assert result.sql == "SELECT * FROM @stage1"
        assert result.substituted == []

    def test_set_then_list_files_keeps_the_stage(self):
        """``SET @stage1 = 1; LIST FILES @stage1`` is a stage browse."""
        session = SessionState()
        handle_set_statement("SET @stage1 = 1", session)
        result = substitute_user_variables("LIST FILES @stage1", session)
        assert result.sql == "LIST FILES @stage1"
        assert result.substituted == []

    def test_set_then_select_scalar_variable_is_unaffected(self):
        """NOVA-25: an ordinary variable read is still substituted.

        ``SELECT @x`` is an expression operand, so the position decides in the
        variable's favour and the stored value is spliced in.
        """
        session = SessionState()
        handle_set_statement("SET @x = 1", session)
        result = substitute_user_variables("SELECT @x", session)
        assert result.sql == "SELECT 1"
        assert result.substituted == ["x"]

    def test_set_then_list_files_unknown_reference_is_left_alone(self):
        """``SET @x`` then ``LIST FILES @x``: the stage wins in that position.

        ``LIST FILES @x`` is a stage browse — ``parser.py`` reads ``@x`` as a
        stage there — so it is not substituted even though a variable named ``x``
        exists. The issue's acceptance list called this "tetap tersubstitusi"
        under the old dot-based rule, but that rule is exactly what made
        ``LIST FILES @stage1`` lose its stage in the repro; the position rule is
        what the issue's own repro table labels ``(BENAR)`` for NOVA-25.
        """
        session = SessionState()
        handle_set_statement("SET @x = 1", session)
        result = substitute_user_variables("LIST FILES @x", session)
        assert result.sql == "LIST FILES @x"
        assert result.substituted == []

    def test_dotted_reference_without_a_variable_is_unchanged_by_the_reorder(self):
        """The reorder does not touch the plain dotted case with no variable."""
        session = SessionState()
        result = substitute_user_variables("SELECT * FROM @stage1.data.csv", session)
        assert result.sql == "SELECT * FROM @stage1.data.csv"
        assert result.substituted == []
        assert result.unknown == []

    @pytest.mark.parametrize(
        "statement",
        [
            "SELECT * FROM @stage1",
            "LIST FILES @stage1",
            "INSERT INTO t SELECT * FROM @stage1",
            "COPY INTO t FROM @stage1",
        ],
    )
    def test_name_clash_leaves_every_stage_position_alone(self, statement):
        """The stage wins wherever ``parser`` reads the position as a stage.

        Parametrised over the stage-introducing keywords so the agreement with
        the dialect classifier is pinned rather than assumed for the one form in
        the report.
        """
        session = self._session(stage1="'CSV_FILE'")
        result = substitute_user_variables(statement, session)
        assert result.sql == statement
        assert result.substituted == []

    @pytest.mark.parametrize(
        "statement",
        [
            "SELECT @stage1",
            "SELECT 1 + @stage1",
            "SELECT f(@stage1)",
            "SELECT * FROM t WHERE a = @stage1",
            "SELECT @stage1, @stage1",
        ],
    )
    def test_same_name_is_still_a_variable_in_expression_position(self, statement):
        """Position, not the name, decides — so the clash cuts both ways."""
        session = self._session(stage1="42")
        result = substitute_user_variables(statement, session)
        assert "@stage1" not in result.sql
        assert result.substituted == ["stage1"] * statement.count("@stage1")

    def test_a_stage_reference_is_reported_as_neither_substituted_nor_unknown(self):
        """Leaving a stage is not the same as failing to resolve a variable."""
        session = self._session(stage1="'CSV_FILE'")
        result = substitute_user_variables("SELECT * FROM @stage1", session)
        assert result.substituted == []
        assert result.unknown == []


class TestDollarInVariableNames:
    """``$`` is legal inside a name on both sides of the session.

    ``_ASSIGNMENT`` accepts it — ``SET @x$abc = 'V'`` stores the key
    ``x$abc`` — so the read half has to accept it too. It is not decoration:
    ``$`` is common in client-side names (``@col$sum``), and a reference regex
    that stops before it both corrupts the statement and makes a stored variable
    unreadable.
    """

    @staticmethod
    def _session(**values: str) -> SessionState:
        session = SessionState()
        session.user_variables = dict(values)
        return session

    def test_a_dollar_name_is_not_split_into_a_prefix_substitution(self):
        """The regression this class exists for.

        Before the accept-set matched ``_ASSIGNMENT``, only ``@x`` was consumed,
        so ``SELECT @x$abc`` became ``SELECT 'V'$abc`` — a different expression
        sent to the engine with no error raised.
        """
        session = self._session(x="'V'")
        result = substitute_user_variables("SELECT @x$abc", session)
        assert result.sql == "SELECT @x$abc"
        assert result.substituted == []

    def test_a_dollar_name_is_never_partially_spliced(self):
        """The failure mode: the value of ``@x`` landing before ``$abc``."""
        session = self._session(x="'V'", x_="'W'")
        result = substitute_user_variables("SELECT @x$abc", session)
        assert "'V'$abc" not in result.sql
        assert "'W'$abc" not in result.sql

    def test_dollar_variable_round_trips_through_set(self):
        """``SET @x$abc = 'V'; SELECT @x$abc`` reads back the stored value."""
        session = SessionState()
        handle_set_statement("SET @x$abc = 'V'", session)
        assert session.user_variables["x$abc"] == "'V'"
        result = substitute_user_variables("SELECT @x$abc", session)
        assert result.sql == "SELECT 'V'"
        assert result.substituted == ["x$abc"]
        assert result.unknown == []

    def test_dollar_variable_is_not_reported_under_the_truncated_name(self):
        """A stored ``x$abc`` must not surface as an unset ``x``.

        The old regex matched ``@x`` and reported ``x`` in ``unknown`` — the
        wrong diagnosis for a variable the proxy itself had accepted.
        """
        session = SessionState()
        handle_set_statement("SET @x$abc = 'DOLLAR_VAL'", session)
        result = substitute_user_variables("SELECT @x$abc", session)
        assert result.unknown == []
        assert result.substituted == ["x$abc"]

    def test_a_realistic_dollar_column_name_substitutes(self):
        """``@col$sum`` is the shape QA cited as reachable in practice."""
        session = self._session(**{"col$sum": "'AGG'"})
        result = substitute_user_variables("SELECT @col$sum", session)
        assert result.sql == "SELECT 'AGG'"
        assert result.substituted == ["col$sum"]

    def test_a_dollar_name_does_not_shadow_a_stage(self):
        """Widening the name does not change which positions are stages."""
        session = self._session(stage1="'CSV_FILE'")
        for statement in (
            "SELECT * FROM @stage1",
            "LIST FILES @stage1",
            "SELECT * FROM @stage1.data.csv",
        ):
            result = substitute_user_variables(statement, session)
            assert result.sql == statement
            assert result.substituted == []

    def test_unset_dollar_name_is_left_for_the_engine(self):
        """An unset ``@x$abc`` is reported whole, not as its ``@x`` prefix."""
        session = SessionState()
        result = substitute_user_variables("SELECT @x$abc", session)
        assert result.sql == "SELECT @x$abc"
        assert result.substituted == []
        assert result.unknown == ["x$abc"]

    def test_dollar_name_case_is_lowercased_like_any_other(self):
        session = SessionState()
        handle_set_statement("SET @X$Abc = 1", session)
        result = substitute_user_variables("SELECT @x$abc", session)
        assert result.sql == "SELECT 1"
        assert result.substituted == ["x$abc"]


class TestLiteralAndCommentSafety:
    """Text that only looks like a reference must survive untouched.

    Split out from ``TestUserVariableSubstitution`` so the tokenizer rules and
    the ``$`` name rules are read separately.
    """

    @staticmethod
    def _session(**values: str) -> SessionState:
        session = SessionState()
        session.user_variables = dict(values)
        return session

    def test_escaped_quote_in_a_literal_does_not_end_the_literal(self):
        """A naive scanner would treat the ``\\'`` as the closing quote."""
        session = self._session(x="1")
        result = substitute_user_variables(r"SELECT 'it\'s @x' AS s", session)
        assert result.sql == r"SELECT 'it\'s @x' AS s"
        assert result.substituted == []

    def test_doubled_quote_in_a_literal_does_not_end_the_literal(self):
        session = self._session(x="1")
        result = substitute_user_variables("SELECT 'it''s @x' AS s", session)
        assert result.sql == "SELECT 'it''s @x' AS s"
        assert result.substituted == []

    def test_substitution_when_a_literal_contains_a_quote_character(self):
        inner = self._session(x="'O''Brien'")
        result = substitute_user_variables("SELECT @x", inner)
        assert result.sql == "SELECT 'O''Brien'"

    def test_name_prefix_is_not_matched(self):
        """``@xy`` is a different variable from ``@x``."""
        session = self._session(x="1")
        result = substitute_user_variables("SELECT @xy", session)
        assert result.sql == "SELECT @xy"
        assert result.substituted == []
        assert result.unknown == ["xy"]

    def test_end_to_end_set_then_select(self):
        """The exact QA reproduction: ``SET @x = '…'; SELECT @x``."""
        session = SessionState()
        handle_set_statement("SET @x = 'QA_MARKER_12345'", session)
        result = substitute_user_variables("SELECT @x AS val", session)
        assert result.sql == "SELECT 'QA_MARKER_12345' AS val"

    def test_no_references_leaves_the_statement_untouched(self):
        session = self._session(x="1")
        sql = "SELECT 1 FROM NOVA_DEMO.customers"
        result = substitute_user_variables(sql, session)
        assert result.sql == sql
        assert result.substituted == []
        assert result.unknown == []


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
