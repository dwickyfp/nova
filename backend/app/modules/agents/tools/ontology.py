"""Business ontology tools composed into the bounded Studio engine.

Both tools read approved, released business meaning that the caller's active
role may see. Neither grants data access: a binding is listed only for a
Semantic View the caller can see and the agent is bound to.
"""

from __future__ import annotations

from fastapi import HTTPException
from pydantic import Field, ValidationError

from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.tools import ToolOutcome
from app.modules.intelligence.contracts import Contract


class ResolveRequest(Contract):
    text: str = Field(min_length=1, max_length=2000)


class ExplainRequest(Contract):
    concept: str = Field(
        min_length=1, max_length=200,
        description="A concept id or qualified name such as Finance.NetRevenue",
    )
    include_related: bool = True


def _release(context) -> str | None:
    from app.modules.ontology.grounding import pinned_release

    return pinned_release(context)


def _remember_release(context, release_id: str | None) -> None:
    if release_id and hasattr(context, "ontology_release_id"):
        context.ontology_release_id = release_id


def _agent_views(context) -> set[str] | None:
    if not getattr(context, "agent_id", None):
        return None
    from app.modules.agents.semantic.access import _context_ids

    return set(_context_ids(context))


def _failure(exc: Exception) -> ToolOutcome:
    return ToolOutcome(ok=False, summary="", error=str(getattr(exc, "detail", exc)))


class OntologyResolveTool:
    name = "ontology_resolve"
    description = (
        "Find the organisation's approved business concepts named in a question, with their "
        "official definitions and the metric bindings that compute them. When one term has "
        "several approved definitions it returns the choices: ask the user which is meant "
        "instead of picking one. Definitions are meaning, not data."
    )
    parameters = ResolveRequest.model_json_schema()
    classification: ToolClassification = "read_only"
    requires_consent = True

    def preview(self, invocation):
        return "Look up approved business definitions"

    async def run(self, invocation, context):
        from app.modules.ontology.resolver import concept_resolver

        try:
            body = ResolveRequest.model_validate(invocation.arguments)
            resolution = await concept_resolver.resolve(
                body.text, context.user, release_id=_release(context)
            )
        except (HTTPException, ValueError, ValidationError) as exc:
            return _failure(exc)
        _remember_release(context, resolution.release_id)
        result = resolution.public()
        views = _agent_views(context)
        if views is not None:
            for concept in result["concepts"]:
                concept["representations"] = [
                    item for item in concept["representations"] if item["semantic_view"] in views
                ]
        summary = {
            "no_release": "No business ontology has been published yet.",
            "none": "The text names no approved business concept.",
            "resolved": f"{len(result['concepts'])} approved concept(s) found.",
            "ambiguous": "A term has several approved definitions; ask which one is meant.",
        }[resolution.status]
        return ToolOutcome(
            ok=True,
            summary=summary,
            data=result,
            evidence={"source": "business_ontology", "release_id": resolution.release_id},
        )


class OntologyExplainTool:
    name = "ontology_explain"
    description = (
        "Explain one approved business concept: its official definition, unit, grain, time "
        "semantics, exclusions, aliases, related concepts and the metrics that compute it. "
        "Related means relevant, never caused by."
    )
    parameters = ExplainRequest.model_json_schema()
    classification: ToolClassification = "read_only"
    requires_consent = True

    def preview(self, invocation):
        return "Explain an approved business concept"

    async def run(self, invocation, context):
        from app.modules.ontology.service import ontology_service

        try:
            body = ExplainRequest.model_validate(invocation.arguments)
            release_id = _release(context)
            concept = await ontology_service.find(body.concept, context.user, release_id=release_id)
            detail = await ontology_service.get_concept(
                concept["id"], context.user, release_id=release_id
            )
            bindings = await ontology_service.representations(
                concept["id"], context.user, release_id=release_id
            )
            related = (
                await ontology_service.related(concept["id"], context.user, release_id=release_id)
                if body.include_related else None
            )
        except (HTTPException, ValueError, ValidationError) as exc:
            return _failure(exc)
        _remember_release(context, detail["release_id"])
        views = _agent_views(context)
        result = {
            "release_id": detail["release_id"],
            "concept": detail["concept"],
            "representations": [
                item for item in bindings["representations"]
                if views is None or item["semantic"]["view_id"] in views
            ],
            "related": (
                {"concepts": related["concepts"], "relations": related["relations"]}
                if related else None
            ),
        }
        from fastapi.encoders import jsonable_encoder

        return ToolOutcome(
            ok=True,
            summary=f"Official definition of {detail['concept']['qualified_name']}.",
            data=jsonable_encoder(result),
            evidence={"source": "business_ontology", "release_id": detail["release_id"]},
        )


ontology_resolve_tool = OntologyResolveTool()
ontology_explain_tool = OntologyExplainTool()
