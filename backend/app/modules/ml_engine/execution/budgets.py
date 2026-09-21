"""Centralized resource budgets for interactive and durable ML work."""

from app.core.config import settings
from app.modules.ml_engine.spec import ExecutionBudget, MLMode


def budget_for(mode: MLMode) -> ExecutionBudget:
    timeouts = {
        MLMode.INTERACTIVE: settings.ML_INTERACTIVE_TIMEOUT_SECONDS,
        MLMode.BALANCED: settings.ML_BALANCED_TIMEOUT_SECONDS,
        MLMode.BEST: settings.ML_BEST_TIMEOUT_SECONDS,
    }
    return ExecutionBudget(
        timeout_seconds=timeouts[mode],
        max_rows=settings.ML_MAX_INTERACTIVE_ROWS,
        max_bytes=settings.ML_MAX_INTERACTIVE_BYTES,
        max_concurrency=settings.ML_MAX_CONCURRENCY,
    )
