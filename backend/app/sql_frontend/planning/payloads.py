from collections.abc import Callable
from typing import Any

from app.sql_frontend.planning.execution import (
    ActionKind,
    ForecastPayload,
    MaterializePayload,
    ModelPayload,
    PasswordPolicyPayload,
    PredictionPayload,
    SecurityPayload,
    SourcePayload,
    TaskPayload,
)

_PAYLOADS: dict[ActionKind, Callable[[int, Any], SourcePayload]] = {
    ActionKind.CREATE_TASK: lambda key, value: TaskPayload(
        key, value.name, value.database_name, value.schema_name, value.schedule_kind, value
    ),
    ActionKind.CREATE_ML_MODEL: lambda key, value: ModelPayload(
        key, value.model_name, value.model_type, value
    ),
    ActionKind.ML_PREDICT: lambda key, value: PredictionPayload(key, value.alias, value),
    ActionKind.ML_MATERIALIZE: lambda key, value: MaterializePayload(key, value[0], value[1]),
    ActionKind.ML_FORECAST: lambda key, value: ForecastPayload(
        key, value.model_alias, value.model_id, value.horizon, value
    ),
    ActionKind.FORCE_PASSWORD_CHANGE: lambda key, value: PasswordPolicyPayload(
        key, value.username, value.required
    ),
    ActionKind.SECURITY: lambda key, value: SecurityPayload(key, value.kind, value.identifiers),
}


def action_payload(action: ActionKind, source_key: int, validated: Any) -> SourcePayload:
    factory = _PAYLOADS.get(action)
    return factory(source_key, validated) if factory else SourcePayload(source_key)
