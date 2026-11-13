from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.sql_frontend.ast import statements as ast


def _model(statement: ast.Statement) -> Any:
    from app.modules.query.dialect.ml_model import parse_create_ml_model

    parsed = statement.parsed
    return parse_create_ml_model(parsed.normalized_sql, tokens=parsed.visible_tokens)


def _task(statement: ast.Statement, context=None) -> Any:
    from app.modules.task_orchestration.ddl import parse_create_task

    return parse_create_task(
        statement.parsed.normalized_sql,
        parsed=statement.parsed,
        database=context.database if context else None,
        schema=context.schema if context else None,
    )


def _execute_task(statement: ast.Statement, context=None) -> Any:
    from app.modules.task_orchestration.ddl import parse_execute_task

    return parse_execute_task(
        statement.parsed.normalized_sql,
        parsed=statement.parsed,
        database=context.database if context else None,
        schema=context.schema if context else None,
    )


def _task_statement(statement: ast.Statement, context=None) -> Any:
    from app.modules.task_orchestration.ddl import parse_task_statement

    return parse_task_statement(
        statement.parsed.normalized_sql,
        parsed=statement.parsed,
        database=context.database if context else None,
        schema=context.schema if context else None,
    )


def _drop_task(statement: ast.Statement, context=None) -> Any:
    from app.modules.task_orchestration.ddl import parse_drop_task

    return parse_drop_task(
        statement.parsed.normalized_sql,
        parsed=statement.parsed,
        database=context.database if context else None,
        schema=context.schema if context else None,
    )


def _predict(statement: ast.MLPredictStatement) -> Any:
    from app.common.ml_intercept import rewrite_ml_predict_projection

    return rewrite_ml_predict_projection(
        statement.parsed.normalized_sql, statement.call, tree=statement.parsed.parse_tree
    )


def _materialize(statement: ast.Statement) -> Any:
    from app.common.ml_intercept import detect_ml_predict_table

    return detect_ml_predict_table(statement.parsed.normalized_sql, parsed=statement.parsed)


def _forecast(statement: ast.Statement) -> Any:
    from app.common.ml_intercept import detect_ml_forecast

    return detect_ml_forecast(statement.parsed.normalized_sql, parsed=statement.parsed)


def _password(statement: ast.Statement) -> Any:
    from app.modules.query.dialect.force_password_change import parse_force_password_change

    return parse_force_password_change(statement.parsed.normalized_sql, parsed=statement.parsed)


def _security(statement: ast.Statement) -> Any:
    from app.sql_frontend.security import decode_security

    return decode_security(statement.parsed)


def _with_context(validator: Callable[..., Any]) -> Callable[..., Any]:
    return lambda statement, context: validator(statement)


_model_context = _with_context(_model)
_predict_context = _with_context(_predict)
_materialize_context = _with_context(_materialize)
_forecast_context = _with_context(_forecast)
_password_context = _with_context(_password)


#: ``sql_dialect`` values the central parser can classify. Nova parses with the
#: pinned StarRocks grammar only; a session switched to another dialect would
#: make the engine read statements Nova classified under different syntax.
_SUPPORTED_SQL_DIALECTS = frozenset({"starrocks"})


def _native_statement_checks(statement: ast.Statement, context: Any = None) -> None:
    """Refuse native statements Nova would misread or that would mislead the engine."""
    _stage_paths_end_cleanly(statement)
    _copy_names_a_stage(statement)
    _native_session_settings(statement)
    _native_pipe_execution(statement)
    _native_background_jobs(statement)
    return None


def _copy_names_a_stage(statement: ast.Statement) -> None:
    """Refuse ``COPY INTO`` with no ``@stage`` on either side.

    The grammar accepts ``COPY INTO t FROM other_table`` and ``FROM (query)``,
    but only stage copies are lowered; the engine has no ``COPY`` and would
    answer with a syntax error that points nowhere useful.
    """
    root = statement.parsed.statement_context
    if type(root).__name__ != "NovaCopyStatementContext":
        return
    from app.sql_frontend.antlr_utils import walk_nodes

    if any(type(node).__name__ == "StageReferenceContext" for node in walk_nodes(root)):
        return
    from app.sql_frontend.errors import SemanticError

    raise SemanticError(
        "COPY INTO reads from or writes to a stage (@stage); "
        "to copy between tables use INSERT INTO ... SELECT"
    )


def _native_pipe_execution(statement: ast.Statement) -> None:
    root = statement.parsed.statement_context
    kind = type(root).__name__
    starts_execution = kind == "CreatePipeStatementContext"
    if kind == "AlterPipeStatementContext":
        starts_execution = root.alterPipeClause().SUSPEND() is None
    if starts_execution:
        from app.sql_frontend.errors import CapabilityUnsupportedError

        raise CapabilityUnsupportedError(
            "Pipes run in the background without your sign-in and active role. "
            "Load files with COPY INTO, or schedule a load with CREATE TASK."
        )


#: Engine statements that start work the engine runs later in the background,
#: as a stored job rather than as the caller's session. Like pipes, that work
#: does not carry the caller's sign-in and active role, so access policies are
#: not applied to it as they are to the caller.
_BACKGROUND_JOB_REFUSALS = {
    "SubmitTaskStatementContext": (
        "SUBMIT TASK runs in the background without your sign-in and active role. "
        "Use CREATE TASK, which runs as you."
    ),
    "CreateRoutineLoadStatementContext": (
        "Routine loads run in the background without your sign-in and active role. "
        "Load files with COPY INTO, or schedule a load with CREATE TASK."
    ),
    "ResumeRoutineLoadStatementContext": (
        "Routine loads run in the background without your sign-in and active role. "
        "Load files with COPY INTO, or schedule a load with CREATE TASK."
    ),
}


def _native_background_jobs(statement: ast.Statement) -> None:
    """Refuse engine background jobs that would not run as the caller.

    Stopping, pausing, showing and dropping existing jobs stays available.
    Nova's ``CREATE TASK`` shares the grammar rule of ``SUBMIT TASK`` and is
    told apart by its first keyword, as the AST builder does.
    """
    parsed = statement.parsed
    kind = type(parsed.statement_context).__name__
    message = _BACKGROUND_JOB_REFUSALS.get(kind)
    if message is None:
        return
    if kind == "SubmitTaskStatementContext" and parsed.visible_tokens[0].text.upper() == "CREATE":
        return
    from app.sql_frontend.errors import CapabilityUnsupportedError

    raise CapabilityUnsupportedError(message)


def _stage_paths_end_cleanly(statement: ast.Statement) -> None:
    """Refuse a block comment glued to the end of an ``@stage`` path.

    ``@stage/*.csv`` reads ``/*`` as the start of a comment: with a later
    ``*/`` in the statement the glob becomes part of the comment and the
    reference silently names the whole stage. The dotted spelling
    (``@stage.*.csv``) has no such reading.
    """
    parsed = statement.parsed
    if "@" not in parsed.normalized_sql:
        return
    from app.sql_frontend.antlr_utils import walk_nodes

    for node in walk_nodes(parsed.statement_context):
        if type(node).__name__ != "StageReferenceContext":
            continue
        stop = node.stop
        tokens = parsed.tokens.tokens
        following = tokens[stop.tokenIndex + 1] if stop.tokenIndex + 1 < len(tokens) else None
        if following is not None and following.start == stop.stop + 1 and (
            following.text.startswith("/*")
        ):
            from app.sql_frontend.errors import SemanticError

            raise SemanticError(
                "A comment starts directly after a stage path; write a glob after a dot "
                "(@stage.*.csv) instead of after a slash"
            )


def _native_session_settings(statement: ast.Statement, context: Any = None) -> None:
    """Refuse native ``SET`` forms that change how the engine parses SQL."""
    root = statement.parsed.statement_context
    if type(root).__name__ != "SetStatementContext":
        return None
    for variable in root.setVar():
        if type(variable).__name__ != "SetSystemVarContext":
            continue
        name = variable.identifier() or variable.systemVariable().identifier()
        if name.getText().strip("`").casefold() != "sql_dialect":
            continue
        value = variable.setExprOrDefault()
        if value.DEFAULT() is not None:
            continue
        if value.getText().strip("'\"`").casefold() not in _SUPPORTED_SQL_DIALECTS:
            from app.sql_frontend.errors import CapabilityUnsupportedError

            raise CapabilityUnsupportedError(
                "Nova parses StarRocks SQL only; sql_dialect must remain StarRocks"
            )
    return None


_TRANSACTION_STATEMENTS = frozenset(
    {"BeginStatementContext", "CommitStatementContext", "RollbackStatementContext"}
)
TRANSACTIONS_UNAVAILABLE = (
    "Transactions are not available in the SQL worksheet: each statement commits when it "
    "runs, so ROLLBACK cannot undo it. Run BEGIN, COMMIT and ROLLBACK from a MySQL client."
)
SESSION_SETTINGS_UNAVAILABLE = (
    "Session settings are not kept in the SQL worksheet: each statement runs separately, "
    "so the setting would not reach the next statement. Set it in the query instead, "
    "for example SELECT /*+ SET_VAR(query_timeout = 600) */ ..."
)


def _session_scoped_setting(variable: Any) -> bool:
    kind = type(variable).__name__
    if kind != "SetSystemVarContext":
        # User variables, SET NAMES and SET TRANSACTION live in the session.
        return kind in {"SetUserVarContext", "SetNamesContext", "SetTransactionContext"}
    scope = variable.varType()
    if scope is None and variable.systemVariable() is not None:
        scope = variable.systemVariable().varType()
    return scope is None or scope.GLOBAL() is None


def refuse_per_statement_session(statement: ast.Statement) -> None:
    """Refuse statements whose only effect is session state a pooled run loses.

    The worksheet runs every statement on its own pooled connection. A
    transaction opened there is never the one a later ``INSERT`` or
    ``ROLLBACK`` runs in, and a session ``SET`` is gone before the next
    statement. Reporting either as successful would be false, so callers that
    run without a held connection refuse them before anything runs. A held
    connection (the MySQL proxy, a task run) keeps its own session and is
    not checked here.
    """
    root = statement.parsed.statement_context
    kind = type(root).__name__
    if kind in _TRANSACTION_STATEMENTS:
        from app.sql_frontend.errors import CapabilityUnsupportedError

        raise CapabilityUnsupportedError(TRANSACTIONS_UNAVAILABLE)
    if kind == "SetStatementContext" and any(
        _session_scoped_setting(variable) for variable in root.setVar()
    ):
        from app.sql_frontend.errors import CapabilityUnsupportedError

        raise CapabilityUnsupportedError(SESSION_SETTINGS_UNAVAILABLE)


def _security_context(statement: ast.Statement, context: Any) -> Any:
    del context
    root = statement.parsed.statement_context
    kind = type(root).__name__
    if kind in {
        "CreateUserStatementContext", "AlterUserStatementContext",
        "DropUserStatementContext", "SetDefaultRoleStatementContext",
    }:
        from app.modules.access_control.service import AccessControlError

        roles = root.roleList() if hasattr(root, "roleList") else None
        if kind == "CreateUserStatementContext" and roles is not None:
            raise AccessControlError("Assign roles through Ranger after creating the account")
        if kind == "SetDefaultRoleStatementContext" or (
            kind == "AlterUserStatementContext" and root.DEFAULT() is not None
        ):
            from app.modules.users.schemas import validate_ranger_default

            names = [] if roles is None else [
                node.getText().strip("`'\"") for node in roles.identifierOrString()
            ]
            validate_ranger_default("explicit", names)
        return None
    return _security(statement)


def validate_action(statement: ast.Statement, context=None) -> Any:
    from app.sql_frontend.analysis.semantics import semantics_registry

    return semantics_registry.validate(statement, context)
