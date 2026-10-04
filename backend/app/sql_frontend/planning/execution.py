from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    from app.common.ml_intercept import MLForecastCall, MLPredictRewrite
    from app.modules.query.dialect.ml_model import CreateMLModelStatement
    from app.modules.task_orchestration.ddl import LoweredTask

from app.sql_frontend.analysis.effects import PlanEffects


class ActionKind(Enum):
    CREATE_TASK = "create_task"
    CREATE_ML_MODEL = "create_ml_model"
    ML_PREDICT = "ml_predict"
    ML_MATERIALIZE = "ml_materialize"
    ML_FORECAST = "ml_forecast"
    SECURITY = "security"
    FORCE_PASSWORD_CHANGE = "force_password_change"


class Atomicity(Enum):
    BEST_EFFORT = "best_effort"
    SINGLE_ENGINE_TRANSACTION = "single_engine_transaction"


@dataclass(frozen=True, slots=True)
class TransactionIntent:
    kind: str
    target: tuple[str, str, str]
    reads: tuple[tuple[str, str, str], ...] = ()
    columns: tuple[str, ...] | None = None
    proven: bool = True


@dataclass(frozen=True, slots=True)
class SourcePayload:
    source_key: int


@dataclass(frozen=True, slots=True)
class TaskPayload(SourcePayload):
    name: str
    database: str | None
    schema: str | None
    schedule_kind: str
    definition: LoweredTask | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class ModelPayload(SourcePayload):
    name: str
    model_type: str
    definition: CreateMLModelStatement | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class PredictionPayload(SourcePayload):
    model_alias: str
    definition: MLPredictRewrite | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class MaterializePayload(SourcePayload):
    model_alias: str
    input_sql: str = field(default="", repr=False)


@dataclass(frozen=True, slots=True)
class ForecastPayload(SourcePayload):
    model_alias: str | None
    model_id: str | None
    horizon: int
    definition: MLForecastCall | None = field(default=None, repr=False)


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
    slot: str = ""
    file_name: str | None = None
    is_directory: bool = False
    access: str = "read"
    scope: tuple[str | None, str | None] = (None, None)
    has_alias: bool = False


@dataclass(frozen=True, slots=True)
class PrivateSqlBinding:
    slot: str
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
    stage_command: str = "stage_query"
    transaction_intent: TransactionIntent | None = None
    private_bindings: tuple[PrivateSqlBinding, ...] = ()

    def __post_init__(self) -> None:
        from app.common.sql_guard import redact_sql_credentials

        if redact_sql_credentials(self.engine_sql) != self.engine_sql:
            raise ValueError("Execution plan SQL must not contain credentials")
        if redact_sql_credentials(self.redacted_sql) != self.redacted_sql:
            raise ValueError("Output SQL must not contain credentials")


@dataclass(frozen=True, slots=True)
class NovaActionPlan:
    action: ActionKind | str
    payload: SourcePayload
    effects: PlanEffects
    requires_confirmation: bool = False

    def __post_init__(self) -> None:
        from app.common.sql_guard import redact_sql_credentials

        def inspect(value):
            if isinstance(value, str) and redact_sql_credentials(value) != value:
                raise ValueError("Execution payload must not contain credentials")
            if isinstance(value, dict):
                for item in value.values():
                    inspect(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    inspect(item)

        inspect(asdict(self.payload))


@dataclass(frozen=True, slots=True)
class CompositePlan:
    steps: tuple[ExecutionPlan, ...]
    atomicity: Atomicity

    def __post_init__(self) -> None:
        if not isinstance(self.atomicity, Atomicity):
            raise ValueError("Composite atomicity must be explicit and typed")

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
