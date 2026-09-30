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


_VALIDATORS: dict[type[ast.Statement], Callable[..., Any]] = {
    ast.CreateMLModelStatement: _model,
    ast.CreateTaskStatement: _task,
    ast.MLPredictStatement: _predict,
    ast.MLMaterializeStatement: _materialize,
    ast.MLForecastStatement: _forecast,
    ast.ForcePasswordChangeStatement: _password,
    ast.SecurityStatement: _security,
}


def validate_action(statement: ast.Statement, context=None) -> Any:
    if isinstance(statement, ast.SecurityStatement) and (
        context is None or not context.ranger_enabled
    ):
        return None
    validator = _VALIDATORS.get(type(statement))
    if validator is _task:
        return validator(statement, context)
    return validator(statement) if validator else None
