from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

from app.sql_frontend.analysis.effects import PlanEffects


class ActionKind(Enum):
    CREATE_TASK = "create_task"
    CREATE_ML_MODEL = "create_ml_model"
    ML_PREDICT = "ml_predict"
    ML_MATERIALIZE = "ml_materialize"
    ML_FORECAST = "ml_forecast"
    SECURITY = "security"
    FORCE_PASSWORD_CHANGE = "force_password_change"


@dataclass(frozen=True, slots=True)
class SourcePayload:
    source_key: int


@dataclass(frozen=True, slots=True)
class TaskPayload(SourcePayload):
    name: str
    database: str | None
    schema: str | None
    schedule_kind: str


@dataclass(frozen=True, slots=True)
class ModelPayload(SourcePayload):
    name: str
    model_type: str


@dataclass(frozen=True, slots=True)
class PredictionPayload(SourcePayload):
    model_alias: str


@dataclass(frozen=True, slots=True)
class MaterializePayload(SourcePayload):
    model_alias: str


@dataclass(frozen=True, slots=True)
class ForecastPayload(SourcePayload):
    model_alias: str | None
    model_id: str | None
    horizon: int


@dataclass(frozen=True, slots=True)
class PasswordPolicyPayload(SourcePayload):
    username: str
    required: bool


@dataclass(frozen=True, slots=True)
class SecurityPayload(SourcePayload):
    operation: str
    identifiers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StageBinding:
    name: str
    path: tuple[str, ...]
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class EngineSqlPlan:
    engine_sql: str
    redacted_sql: str
    effects: PlanEffects
    source_key: int = 0
    stage_aware: bool = False
    requires_confirmation: bool = False
    stage_bindings: tuple[StageBinding, ...] = ()

    def __post_init__(self) -> None:
        from app.common.sql_guard import redact_sql_credentials

        if redact_sql_credentials(self.engine_sql) != self.engine_sql:
            raise ValueError("Execution plan SQL must not contain credentials")
        if redact_sql_credentials(self.redacted_sql) != self.redacted_sql:
            raise ValueError("Output SQL must not contain credentials")


@dataclass(frozen=True, slots=True)
class NovaActionPlan:
    action: ActionKind
    payload: SourcePayload
    effects: PlanEffects
    requires_confirmation: bool = False


@dataclass(frozen=True, slots=True)
class CompositePlan:
    steps: tuple[ExecutionPlan, ...]

    @property
    def effects(self) -> PlanEffects:
        effects = PlanEffects()
        for step in self.steps:
            effects |= step.effects
        return effects

    @property
    def requires_confirmation(self) -> bool:
        return any(step.requires_confirmation for step in self.steps)


ExecutionPlan: TypeAlias = EngineSqlPlan | NovaActionPlan | CompositePlan
