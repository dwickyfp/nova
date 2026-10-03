"""Typed parameters shared by HTTP and bounded agent numerical operations."""

from typing import Literal

from pydantic import Field

from app.modules.intelligence.contracts import Contract, SemanticRef
from app.modules.ml_engine.decision_lab import SimulationInput


class AnalysisRequest(Contract):
    operation_id: str = Field(min_length=8, max_length=128)
    method: Literal["change_points", "key_drivers", "correlation", "forecast", "causal_effect"]
    semantic: SemanticRef
    plan: dict
    prior_plan: dict | None = None
    value_column: str = Field(min_length=1, max_length=128)
    comparison_column: str | None = Field(default=None, max_length=128)
    timestamp_column: str | None = Field(default=None, max_length=128)
    dimension: str | None = Field(default=None, max_length=128)
    experiment: str | None = Field(default=None, max_length=128)
    minimum_segment: int = Field(default=7, ge=3, le=100)
    horizon: int = Field(default=7, ge=1, le=90)
    frequency: Literal["D", "h", "W", "MS"] = "D"


class OptimizationRequest(Contract):
    operation_id: str = Field(min_length=8, max_length=128)
    options: list[SimulationInput] = Field(min_length=1, max_length=30)
    minimum_gross_profit: float = 0
    maximum_cost: float = Field(ge=0)
