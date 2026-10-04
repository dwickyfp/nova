"""Public Mission transport and sanitization of already-authorized workflow replay."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from app.modules.assistant.evidence_health import EvidenceHealth
from app.modules.intelligence.public_projections import (
    PublicCanonicalObservation,
    PublicContract,
    PublicEvidenceRef,
    PublicInvestigation,
    PublicSemanticRef,
    PublicWindow,
    private_workflow_field,
    projection_input,
    public_action,
    public_canonical_record,
    public_investigation,
)


class PublicMissionStage(PublicContract):
    kind: str
    label: str
    status: Literal["planned", "running", "completed", "blocked", "cancelled"]
    source_refs: list[str] = Field(default_factory=list, max_length=100)


class PublicObjectRef(PublicContract):
    kind: Literal["investigation", "decision", "action", "outcome"]
    id: str
    revision: int = Field(ge=1)


class PublicContinuation(PublicContract):
    mode: Literal["continue", "new", "none"]
    reason: str
    mission_id: str | None = None


class PublicSemanticAnchor(PublicContract):
    semantic: PublicSemanticRef
    metrics: list[str] = Field(max_length=32)


class PublicFilterShape(PublicContract):
    field: str
    operator: str


class PublicWorkflowProvenance(PublicContract):
    mission_id: str | None = Field(default=None, max_length=128)
    run_id: str | None = Field(default=None, max_length=128)
    root_run_id: str | None = Field(default=None, max_length=128)


class PublicEvidenceEnvelope(PublicContract):
    schema_version: Literal[1] = 1
    health: EvidenceHealth
    semantic: PublicSemanticRef | None = None
    metrics: list[str] = Field(default_factory=list, max_length=32)
    dimensions: list[str] = Field(default_factory=list, max_length=32)
    current_window: PublicWindow | None = None
    baseline_window: PublicWindow | None = None
    timezone: str | None = None
    filter_shape: list[PublicFilterShape] = Field(default_factory=list, max_length=32)
    named_filters: list[str] = Field(default_factory=list, max_length=32)
    warnings: list[str] = Field(default_factory=list, max_length=32)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    validated_plan_fingerprint: str
    model_fingerprint: str
    mission_id: str | None = None
    run_id: str | None = None
    root_run_id: str | None = None
    workflow: PublicWorkflowProvenance | None = None


class PublicInvestigationRequirements(PublicContract):
    required_inputs: list[str] = Field(max_length=16)
    established: PublicEvidenceEnvelope


class PublicMission(PublicContract):
    mission_id: str
    thread_id: str
    agent_id: str | None = None
    objective: str = Field(min_length=1, max_length=4000)
    work_intent: Literal["ANSWER", "ANALYZE", "INVESTIGATE", "PLAN", "RESEARCH", "ACT"]
    status: Literal["planned", "running", "completed", "blocked", "cancelling", "cancelled"]
    revision: int = Field(ge=1)
    run_ids: list[str] = Field(default_factory=list, max_length=100)
    stages: list[PublicMissionStage] = Field(default_factory=list, max_length=9)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    object_refs: list[PublicObjectRef] = Field(default_factory=list, max_length=100)
    cancel_requested: bool = False
    cancellation_complete: bool = False
    created_at: datetime
    updated_at: datetime
    semantic_anchors: list[PublicSemanticAnchor] = Field(default_factory=list, max_length=32)
    continuation: PublicContinuation | None = None
    investigation_requirements: PublicInvestigationRequirements | None = None


class PublicMissionList(PublicContract):
    missions: list[PublicMission]


class PublicResumableMission(PublicContract):
    mission_id: str
    thread_id: str
    objective: str
    status: str
    revision: int
    resume_required: bool


class PublicResumableMissionList(PublicContract):
    missions: list[PublicResumableMission]


class PublicDeliverableSource(PublicContract):
    kind: str
    id: str
    revision: int
    fingerprint: str
    semantic: PublicSemanticRef | None = None


class PublicMissionDeliverable(PublicContract):
    deliverable_id: str
    mission_id: str
    mission_revision: int
    kind: str
    title: str
    markdown: str = Field(max_length=32000)
    evidence_refs: list[str] = Field(max_length=100)
    object_refs: list[PublicObjectRef] = Field(max_length=100)
    created_at: datetime
    sources: list[PublicDeliverableSource] = Field(default_factory=list, max_length=201)


class PublicDeliverableList(PublicContract):
    deliverables: list[PublicMissionDeliverable]


class PublicBusinessResult(PublicContract):
    status: str
    reason: str | None = None
    mission_id: str | None = None
    run_id: str | None = None
    root_run_id: str | None = None
    required_inputs: list[str] = Field(default_factory=list, max_length=16)
    established: PublicEvidenceEnvelope | None = None
    investigation: PublicInvestigation | None = None
    comparison_id: str | None = None
    mission: PublicMission | None = None
    error_code: int | str | None = None
    workflow: PublicWorkflowProvenance | None = None


def public_mission(value: BaseModel | Mapping[str, Any]) -> PublicMission:
    return PublicMission.model_validate(projection_input(value))


def public_business_result(value: BaseModel | Mapping[str, Any]) -> dict[str, Any]:
    return PublicBusinessResult.model_validate(projection_input(value)).model_dump(mode="json")


def sanitize_workflow_payload(value: Any) -> Any:
    """Copy public workflow fragments; do not authorize or reconstruct business state.

    Legacy transcripts can lack fields required by current response contracts.
    Malformed workflow fragments are omitted rather than returning their raw contents.
    """
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, (list, tuple)):
        return [sanitize_workflow_payload(item) for item in value]
    if not isinstance(value, Mapping):
        return None if isinstance(value, (bytes, bytearray)) else value
    if value.get("kind") == "investigation" and "target_metric" in value:
        return _legacy_projection(
            PublicCanonicalObservation.model_validate, value, exclude_unset=True
        )
    if "mission_id" in value and "objective" in value:
        return _legacy_projection(public_mission, value)
    if "news_id" in value and "hypotheses" in value:
        return _legacy_projection(public_investigation, value)
    if "adapter_id" in value and "decision_id" in value and "status" in value:
        return _legacy_projection(public_action, value)
    for kind, required in (
        ("decisions", {"investigation_id", "target_metric", "options"}),
        ("outcomes", {"decision_id", "predicted", "attribution"}),
        ("news", {"monitor_id", "before", "after", "summary"}),
        ("monitors", {"id", "name", "plan", "time_dimension"}),
        ("comparisons", {"monitor_id", "current_window", "baseline_window", "status"}),
        ("events", {"decision_id", "decision_revision", "event"}),
        ("action_events", {"action_id", "action_revision", "event"}),
        ("nodes", {"id", "reference_id", "kind", "state"}),
        ("edges", {"id", "source", "target", "relationship"}),
    ):
        if required <= value.keys():
            try:
                return public_canonical_record(kind, value)
            except (ValidationError, TypeError, ValueError):
                return None
    if "source_type" in value and "source_id" in value:
        return _legacy_projection(PublicEvidenceRef.model_validate, value)
    if "health" in value and "validated_plan_fingerprint" in value:
        return _legacy_projection(PublicEvidenceEnvelope.model_validate, value)
    output = {}
    for key, item in value.items():
        if (
            key == "scope"
            and isinstance(item, str)
            and item
            in {
                "current_loop_attempt",
                "current_operation_attempt",
            }
        ):
            output[key] = item
            continue
        if private_workflow_field(str(key)):
            continue
        if key == "workflow" and item is not None:
            output[key] = _legacy_projection(
                PublicWorkflowProvenance.model_validate,
                item,
                exclude_unset=True,
            )
        elif key == "canonical_business_result" and item is not None:
            if isinstance(item, Mapping) and item.get("kind") == "investigation":
                output[key] = _legacy_projection(
                    PublicCanonicalObservation.model_validate,
                    item,
                    exclude_unset=True,
                )
            else:
                output[key] = sanitize_workflow_payload(item)
        elif key == "business_result":
            output[key] = _legacy_projection(PublicBusinessResult.model_validate, item)
        elif key == "mission" and item is not None:
            output[key] = _legacy_projection(public_mission, item)
        elif key == "investigation" and item is not None:
            output[key] = _legacy_projection(public_investigation, item)
        else:
            output[key] = sanitize_workflow_payload(item)
    return output


def _legacy_projection(project, value, *, exclude_unset: bool = False):
    try:
        result = project(value)
        return result.model_dump(mode="json", exclude_unset=exclude_unset)
    except (ValidationError, TypeError, ValueError):
        return None


def sanitize_workflow_sse(frame: str) -> str:
    """Sanitize saved frames without allocating a new event sequence or changing IDs."""
    lines = frame.splitlines(keepends=True)
    data_indices = [index for index, line in enumerate(lines) if line.startswith("data:")]
    if not data_indices:
        return frame
    event = next((line[6:].strip() for line in lines if line.startswith("event:")), "")
    raw = "\n".join(lines[index][5:].lstrip().rstrip("\r\n") for index in data_indices)
    try:
        payload = json.loads(raw)
    except ValueError:
        if event not in {"business_result", "mission_updated", "evidence_envelope"}:
            return frame
        lines[data_indices[0]] = 'data: {"reason":"invalid_workflow_projection"}\n'
        for index in reversed(data_indices[1:]):
            del lines[index]
        return "".join(lines)
    if event == "business_result":
        # Event envelopes also carry public run IDs and sequence independently of the result.
        safe = _legacy_projection(PublicBusinessResult.model_validate, payload) or {
            "status": "blocked",
            "reason": "invalid_workflow_projection",
        }
        for key in ("run_id", "root_run_id", "sequence"):
            if isinstance(payload, dict) and key in payload:
                item = payload[key]
                if (key == "sequence" and type(item) is int) or (
                    key != "sequence" and isinstance(item, str) and len(item) <= 64
                ):
                    safe[key] = item
    else:
        safe = sanitize_workflow_payload(payload)
    if safe == payload:
        return frame
    encoded = json.dumps(safe, separators=(",", ":"), ensure_ascii=False)
    first, *rest = data_indices
    ending = "\r\n" if lines[first].endswith("\r\n") else "\n"
    lines[first] = f"data: {encoded}{ending}"
    for index in reversed(rest):
        del lines[index]
    return "".join(lines)
