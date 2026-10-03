"""Intelligence tools composed into the existing bounded Studio engine."""

from typing import Literal

from fastapi import HTTPException
from pydantic import Field, ValidationError

from app.modules.assistant.tools import ToolOutcome
from app.modules.intelligence.contracts import Contract, SemanticRef
from app.modules.intelligence.responses import public_evidence


def scoped_user(context):
    from app.modules.agents.semantic.access import _context_ids

    user = dict(context.user)
    if getattr(context, "agent_id", None):
        user["intelligence_allowed_views"] = _context_ids(context)
    return user


class ContextRequest(Contract):
    semantic: SemanticRef | None = None
    term: str | None = Field(default=None, max_length=256)
    node_id: str | None = Field(default=None, max_length=128)
    depth: int = Field(default=2, ge=0, le=4)
    record_kind: Literal["news", "investigations", "decisions", "outcomes"] | None = None
    record_id: str | None = Field(default=None, max_length=64)


class ContextGraphTool:
    name = "context_graph"
    description = (
        "Resolve a metric name against a published Semantic View or inspect authorized context "
        "references around a node, or open a News, investigation, decision or outcome reference. "
        "Competing definitions require clarification; hypotheses do "
        "not establish facts. Returned references identify their source and version."
    )
    parameters = ContextRequest.model_json_schema()
    classification = "read_only"
    requires_consent = True

    def preview(self, invocation):
        return "Inspect governed business context"

    async def run(self, invocation, context):
        from app.modules.intelligence.context_graph import resolve_metric, traverse_context

        try:
            body = ContextRequest.model_validate(invocation.arguments)
            if body.semantic and body.term:
                result = await resolve_metric(body.semantic, body.term, scoped_user(context))
                result["candidates"] = [
                    {**row, "semantic": row["semantic"].model_dump(mode="json")}
                    for row in result["candidates"]
                ]
            elif body.node_id:
                result = await traverse_context(
                    body.node_id, scoped_user(context), depth=body.depth, limit=30
                )
                result = {
                    **result,
                    "nodes": [row.model_dump(mode="json") for row in result["nodes"]],
                    "edges": [row.model_dump(mode="json") for row in result["edges"]],
                }
            elif body.record_kind and body.record_id:
                from app.modules.intelligence.engine import intelligence_service

                record = await intelligence_service.get(
                    body.record_kind, body.record_id, scoped_user(context)
                )
                result = record.model_dump(mode="json")
            else:
                raise ValueError("Specify a Semantic View and metric term, or a context node")
        except (HTTPException, ValueError, ValidationError) as exc:
            return ToolOutcome(ok=False, summary="", error=str(getattr(exc, "detail", exc)))
        return ToolOutcome(
            ok=True,
            summary="Governed context inspected; source authority is explicit.",
            data=public_evidence(result),
            evidence={"source": "context_graph"},
        )


class DecisionLabTool:
    name = "decision_lab"
    description = (
        "Run change-point, dimensional driver, correlation, chronological forecast, or randomized "
        "effect analysis over a published semantic plan. Causal effects require a reviewed "
        "experiment protocol in the published Semantic View; correlation alone is association."
    )
    classification = "read_only"
    requires_consent = True

    @property
    def parameters(self):
        from app.modules.ml_engine.analysis_contracts import AnalysisRequest

        return AnalysisRequest.model_json_schema()

    def preview(self, invocation):
        return "Run a bounded numerical analysis of governed data"

    async def run(self, invocation, context):
        from fastapi.encoders import jsonable_encoder

        from app.modules.intelligence.decision_lab import AnalysisRequest, analyze

        try:
            body = AnalysisRequest.model_validate(invocation.arguments)
            result = await analyze(body, scoped_user(context))
        except (HTTPException, ValueError, ValidationError) as exc:
            return ToolOutcome(ok=False, summary="", error=str(getattr(exc, "detail", exc)))
        return ToolOutcome(
            ok=True,
            summary=f"Numerical analysis complete using {result['method']}.",
            data=public_evidence(jsonable_encoder(result)),
            evidence={"source": "decision_lab", "run_id": result["run_id"]},
        )


context_graph_tool = ContextGraphTool()
decision_lab_tool = DecisionLabTool()
