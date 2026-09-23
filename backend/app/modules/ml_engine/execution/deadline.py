"""One monotonic deadline shared by extraction, fitting, and finalization."""

import time
from contextvars import ContextVar
from dataclasses import dataclass

from app.modules.ml_engine.spec import MLExecutionTimeout

STAGES = (
    "startup",
    "extraction",
    "training",
    "serialization",
    "artifact_upload",
    "inference",
    "registration",
)
phase_state = None
current_stage = ContextVar("ml_execution_stage", default="startup")


@dataclass(frozen=True)
class ExecutionDeadline:
    expires_at: float

    def remaining(self, stage: str, *, reserve: float = 0) -> float:
        current_stage.set(stage)
        if phase_state is not None and stage in STAGES:
            phase_state.value = STAGES.index(stage)
        value = self.expires_at - time.monotonic() - reserve
        if value <= 0:
            raise MLExecutionTimeout(stage)
        return value
