"""Centralized resource budgets for interactive and durable ML work."""

from app.core.config import settings
from app.modules.ml_engine.spec import ExecutionBudget, MLMode


def budget_for(mode: MLMode) -> ExecutionBudget:
    timeouts = {
        MLMode.INTERACTIVE: settings.ML_INTERACTIVE_TIMEOUT_SECONDS,
        MLMode.BALANCED: settings.ML_BALANCED_TIMEOUT_SECONDS,
        MLMode.BEST: settings.ML_BEST_TIMEOUT_SECONDS,
    }
    rows = {
        MLMode.INTERACTIVE: settings.ML_MAX_INTERACTIVE_ROWS,
        MLMode.BALANCED: settings.ML_MAX_BALANCED_ROWS,
        MLMode.BEST: settings.ML_MAX_BEST_ROWS,
    }
    bytes_ = {
        MLMode.INTERACTIVE: settings.ML_MAX_INTERACTIVE_BYTES,
        MLMode.BALANCED: settings.ML_MAX_BALANCED_BYTES,
        MLMode.BEST: settings.ML_MAX_BEST_BYTES,
    }
    return ExecutionBudget(
        timeout_seconds=timeouts[mode],
        max_rows=rows[mode],
        max_bytes=bytes_[mode],
        max_concurrency=settings.ML_MAX_CONCURRENCY,
    )
