"""Bounded provenance shared by live Studio evidence and persisted replay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import Field, model_validator

from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.planning import SemanticPlan
from app.modules.agents.semantic.time_ranges import ExecutionTimeContext
from app.modules.assistant.evidence_health import EvidenceHealth
from app.modules.intelligence.contracts import Contract, SemanticRef, Window, fingerprint


class FilterShape(Contract):
    field: str = Field(min_length=1, max_length=128)
    operator: str = Field(min_length=1, max_length=32)


class EvidenceEnvelope(Contract):
    schema_version: Literal[1] = 1
    health: EvidenceHealth
    semantic: SemanticRef | None = None
    metrics: list[str] = Field(default_factory=list, max_length=32)
    dimensions: list[str] = Field(default_factory=list, max_length=32)
    current_window: Window | None = None
    baseline_window: Window | None = None
    timezone: str | None = Field(default=None, max_length=128)
    filter_shape: list[FilterShape] = Field(default_factory=list, max_length=32)
    named_filters: list[str] = Field(default_factory=list, max_length=32)
    warnings: list[str] = Field(default_factory=list, max_length=32)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    validated_plan_fingerprint: str = Field(min_length=64, max_length=64)
    model_fingerprint: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def bounded_references(self):
        for values in (self.metrics, self.dimensions, self.named_filters, self.evidence_refs):
            if any(not item or len(item) > 128 for item in values):
                raise ValueError("Evidence references exceed their bound")
        if any(len(item) > 256 for item in self.warnings):
            raise ValueError("Evidence warnings exceed their bound")
        return self


@dataclass(frozen=True)
class InvestigationSeed:
    """Internal validated execution input; never a provider or client authority claim."""

    semantic: SemanticRef
    plan: SemanticPlan
    target_metric: str
    driver_dimensions: tuple[str, ...]
    execution_time: ExecutionTimeContext
    plan_fingerprint: str


def semantic_population_fingerprint(plan: SemanticPlan) -> str:
    """Identify the selected population without retaining filter literals."""
    values = plan.as_dict()
    return fingerprint({
        "filters": sorted(fingerprint(item) for item in values.get("filters", [])),
        "named_filters": sorted(values.get("named_filters", [])),
        "having": sorted(fingerprint(item) for item in values.get("having", [])),
    })


def execution_envelope(
    model: dict, ir: SemanticModelIR, plan: SemanticPlan, health: EvidenceHealth,
    time_context: ExecutionTimeContext | None, *, evidence_id: str,
) -> EvidenceEnvelope:
    semantic = None
    if model.get("version") and model.get("fingerprint"):
        semantic = SemanticRef(
            view_id=str(model.get("id") or model.get("semantic_model_id")),
            version=model["version"], fingerprint=model["fingerprint"],
        )
    return EvidenceEnvelope(
        health=health, semantic=semantic, metrics=list(plan.metrics),
        dimensions=list(plan.dimensions),
        current_window=Window(**time_context.current.as_dict()) if time_context else None,
        baseline_window=Window(**time_context.baseline.as_dict())
        if time_context and time_context.baseline else None,
        timezone=time_context.timezone if time_context else None,
        filter_shape=[FilterShape(field=item.field, operator=item.operator)
                      for item in plan.filters],
        named_filters=list(plan.named_filters),
        warnings=list(time_context.warnings) if time_context else [],
        evidence_refs=[evidence_id[:128]], validated_plan_fingerprint=fingerprint(plan.as_dict()),
        model_fingerprint=ir.fingerprint,
    )


def investigation_seed(
    envelope: EvidenceEnvelope, ir: SemanticModelIR, plan: SemanticPlan,
    time_context: ExecutionTimeContext | None,
) -> tuple[InvestigationSeed | None, list[str]]:
    missing = []
    if envelope.semantic is None:
        missing.append("published_semantic_identity")
    if len(plan.metrics) != 1:
        missing.append("one_target_metric")
    if not time_context or not time_context.baseline:
        missing.append("comparison_windows")
    if envelope.health.facts.coverage != "complete":
        missing.append("complete_execution")
    if plan.having or plan.transforms or plan.top_n_per_group or plan.unresolved_concepts:
        missing.append("scalar_comparison_plan")
    if missing:
        return None, missing
    metric = ir.metric(plan.metrics[0])
    if metric is None:
        return None, ["authorized_target_metric"]
    if metric.additivity.value != "additive":
        return None, ["additive_target_metric"]
    dimensions = tuple(plan.dimensions[:3])
    if not dimensions:
        dimensions = tuple(
            f"{dataset.name}.{field.name}" for dataset in ir.datasets
            if dataset.name == metric.base_dataset for field in dataset.fields
            if field.kind.value == "dimension" and not field.is_time
        )[:3]
    return InvestigationSeed(
        envelope.semantic, plan, plan.metrics[0], dimensions, time_context,
        envelope.validated_plan_fingerprint,
    ), []
