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


def _security_context(statement: ast.Statement, context: Any) -> Any:
    return _security(statement) if context and context.ranger_enabled else None


def validate_action(statement: ast.Statement, context=None) -> Any:
    from app.sql_frontend.analysis.semantics import semantics_registry

    return semantics_registry.validate(statement, context)
