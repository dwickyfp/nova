"""Business ontology tools composed into the bounded Studio engine.

Both tools read approved, released business meaning that the caller's active
role may see. Neither grants data access: a binding is listed only for a
Semantic View the caller can see and the agent is bound to.
"""

from __future__ import annotations

from typing import Literal

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
        "Answer 'what does this term mean' questions: find the organisation's approved "
        "business concepts named in a text, with their official definitions and the metrics "
        "that compute them. The result already contains each definition, so do not also call "
        "ontology_explain for the same term. When one term has several approved definitions "
        "it returns the choices: ask the user which is meant instead of picking one. "
        "Definitions are meaning, not data."
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
            # A term means what it means in this agent's business (its views).
            resolution = await concept_resolver.resolve(
                body.text,
                context.user,
                release_id=_release(context),
                scope_views=_agent_views(context),
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
        "Go deeper on one concept you already know by id or qualified name (for example "
        "Finance.NetRevenue): its unit, grain, time semantics, exclusions, related concepts "
        "and metric bindings. For what a term in the user's words means, use "
        "ontology_resolve instead. Related means relevant, never caused by."
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
            "related": await _model_related(related, concept["id"], context.user),
        }
        from fastapi.encoders import jsonable_encoder

        return ToolOutcome(
            ok=True,
            summary=f"Official definition of {detail['concept']['qualified_name']}.",
            data=jsonable_encoder(result),
            evidence={"source": "business_ontology", "release_id": detail["release_id"]},
        )


async def _model_related(related: dict | None, root: str, user: dict) -> dict | None:
    """Related concepts a model may read; relations to the rest are dropped."""
    if not related:
        return None
    from app.modules.ontology import access
    from app.modules.ontology.service import ontology_service

    snapshot = await ontology_service.snapshot("current", release_id=related.get("release_id"))
    allowed = {root} | {
        item["id"] for item in related["concepts"]
        if access.model_may_see(
            item["classification"], snapshot.domains.get(item["domain_id"]),
            snapshot.domains, user,
        )
    }
    return {
        "concepts": [item for item in related["concepts"] if item["id"] in allowed],
        "relations": [
            edge for edge in related["relations"]
            if edge["source_id"] in allowed and edge["target_id"] in allowed
        ],
    }


ontology_resolve_tool = OntologyResolveTool()
ontology_explain_tool = OntologyExplainTool()


class NewConcept(Contract):
    domain: str = Field(min_length=1, max_length=200, description="Domain path, e.g. Finance")
    slug: str = Field(min_length=1, max_length=64, description="Name such as NetRevenue")
    label: str = Field(min_length=1, max_length=200)
    kind: str = Field(default="METRIC", max_length=32)
    definition: str = Field(min_length=1, max_length=8000)
    unit: str | None = Field(default=None, max_length=64)


class ProposeRequest(Contract):
    change: Literal["add_aliases", "revise_definition", "new_concept"]
    concept: str | None = Field(
        default=None, max_length=200,
        description="Existing concept id or qualified name, for add_aliases and revise_definition",
    )
    aliases: list[str] = Field(default_factory=list, max_length=16)
    definition: str | None = Field(default=None, max_length=8000)
    new_concept: NewConcept | None = None
    rationale: str = Field(min_length=1, max_length=2000)


class KnowledgeProposeTool:
    name = "knowledge_propose"
    description = (
        "Suggest a change to the organisation's business definitions when the user corrects or "
        "teaches a term: add other names (aliases), revise a definition, or propose a new "
        "concept. It only files a proposal: a domain steward must approve it and a release must "
        "be published before assistants use it. Never claim the definition has changed."
    )
    parameters = ProposeRequest.model_json_schema()
    #: It files a reviewable proposal; approved meaning never changes here.
    classification: ToolClassification = "session_change"
    requires_consent = True

    def preview(self, invocation):
        return "Propose a change to a business definition for steward review"

    async def run(self, invocation, context):
        from app.modules.intelligence.contracts import fingerprint
        from app.modules.ontology.stewardship import ProposalCreate, stewardship_service

        try:
            body = ProposeRequest.model_validate(invocation.arguments)
            changes, title = await _proposed_changes(body, context.user)
            proposal = await stewardship_service.create(
                ProposalCreate(
                    title=title,
                    rationale=f"Suggested in a conversation: {body.rationale}",
                    changes=changes,
                    operation_id=fingerprint(
                        [getattr(context, "run_id", None), invocation.tool_call_id]
                    )[:48],
                ),
                context.user,
                source="AGENT",
            )
        except (HTTPException, ValueError, ValidationError) as exc:
            return _failure(exc)
        return ToolOutcome(
            ok=True,
            summary=(
                f"Proposal filed for steward review ({proposal.title}). Nothing changes until a "
                "steward approves it and a release is published."
            ),
            data={"proposal_id": proposal.id, "status": proposal.status, "title": proposal.title},
            evidence={"source": "ontology_proposal", "proposal_id": proposal.id},
        )


async def _proposed_changes(body: ProposeRequest, user: dict):
    from app.modules.ontology.contracts import ConceptBody, ConceptChange, ConceptKind
    from app.modules.ontology.service import ontology_service

    if body.change == "new_concept":
        if body.new_concept is None:
            raise ValueError("A new concept needs new_concept")
        domains = [
            item for item in await ontology_service.list_domains(user)
            if item["qualified_path"].casefold() == body.new_concept.domain.casefold()
            and item["status"] == "ACTIVE"
        ]
        if not domains:
            raise ValueError(f"No active domain {body.new_concept.domain} you can see")
        concept = body.new_concept
        change = ConceptChange(
            op="create",
            body=ConceptBody(
                domain_id=domains[0]["id"], kind=ConceptKind(concept.kind.upper()),
                slug=concept.slug,
                label=concept.label, definition=concept.definition,
                aliases=body.aliases, unit=concept.unit,
            ),
        )
        return [change], f"Add {domains[0]['qualified_path']}.{concept.slug}"
    if not body.concept:
        raise ValueError("Name the concept to change")
    snapshot = await ontology_service.snapshot("working")
    # A non-steward can only name what was released; unreleased work stays
    # unseen, and is refused like a missing concept.
    visible = ontology_service.visible_in(snapshot, "working", user)
    released = await ontology_service.snapshot("current")
    for concept_id in ontology_service.visible_concepts(released, user):
        if concept_id not in visible and concept_id in snapshot.concepts:
            visible[concept_id] = snapshot.concepts[concept_id]
    wanted = body.concept.strip().casefold()
    current = visible.get(body.concept) or next(
        (
            item for item in visible.values()
            if ontology_service.concept_view(item, snapshot.domains)["qualified_name"].casefold()
            == wanted
        ),
        None,
    )
    if current is None or current.status != "APPROVED":
        raise ValueError("Ontology record not found")
    name = ontology_service.concept_view(current, snapshot.domains)["qualified_name"]
    update: dict = {}
    if body.change == "add_aliases":
        if not body.aliases:
            raise ValueError("Give the aliases to add")
        update["aliases"] = [*current.aliases, *body.aliases]
        title = f"Add other names to {name}"
    else:
        if not body.definition:
            raise ValueError("Give the revised definition")
        update["definition"] = body.definition
        title = f"Revise the definition of {name}"
    fields = ConceptBody.model_fields
    revised = ConceptBody.model_validate(
        {**{key: getattr(current, key) for key in fields}, **update}
    )
    return [
        ConceptChange(
            op="update", concept_id=current.id, base_revision=current.revision, body=revised
        )
    ], title


knowledge_propose_tool = KnowledgeProposeTool()


class StatusRequest(Contract):
    limit: int = Field(default=10, ge=1, le=25)


class OntologyStatusTool:
    name = "ontology_status"
    description = (
        "Which business-definition release is in use (when it was published and its note) "
        "and the definition proposals the user filed, with their review status. Use it when "
        "the user asks whether a suggestion was approved or what changed in the definitions."
    )
    parameters = StatusRequest.model_json_schema()
    classification: ToolClassification = "read_only"
    requires_consent = True

    def preview(self, invocation):
        return "Check the business-definition release and your proposals"

    async def run(self, invocation, context):
        from app.modules.ontology.contracts import Actor
        from app.modules.ontology.releases import release_service
        from app.modules.ontology.stewardship import stewardship_service

        try:
            body = StatusRequest.model_validate(invocation.arguments)
            alias = await release_service.alias()
            release = await release_service.get(alias.release_id) if alias else None
            me = Actor.from_user(context.user).principal
            mine = [
                item for item in await stewardship_service.list(context.user)
                if item.submitter == me
            ][: body.limit]
        except (HTTPException, ValueError, ValidationError) as exc:
            return _failure(exc)
        data = {
            "release": {
                "id": release.id,
                "sequence": release.sequence,
                "published_at": release.published_at,
                "note": release.note,
            } if release else None,
            "my_proposals": [
                {"id": item.id, "title": item.title, "status": item.status,
                 "created_at": item.created_at}
                for item in mine
            ],
        }
        from fastapi.encoders import jsonable_encoder

        summary = (
            "No business definitions have been published yet."
            if release is None
            else f"Release {release.sequence} is in use; you filed {len(mine)} proposal(s)."
        )
        return ToolOutcome(
            ok=True, summary=summary, data=jsonable_encoder(data),
            evidence={"source": "business_ontology",
                      "release_id": release.id if release else None},
        )


class WithdrawRequest(Contract):
    proposal_id: str = Field(min_length=1, max_length=64)


class KnowledgeWithdrawTool:
    name = "knowledge_withdraw"
    description = (
        "Withdraw a business-definition proposal the user filed and that is still waiting "
        "for review. Only the person who filed it can withdraw it."
    )
    parameters = WithdrawRequest.model_json_schema()
    classification: ToolClassification = "session_change"
    requires_consent = True

    def preview(self, invocation):
        return "Withdraw your business-definition proposal"

    async def run(self, invocation, context):
        from app.modules.ontology.stewardship import stewardship_service

        try:
            body = WithdrawRequest.model_validate(invocation.arguments)
            proposal = await stewardship_service.withdraw(body.proposal_id, context.user)
        except (HTTPException, ValueError, ValidationError) as exc:
            return _failure(exc)
        return ToolOutcome(
            ok=True,
            summary=f"The proposal \"{proposal.title}\" is withdrawn; nothing was changed.",
            data={"proposal_id": proposal.id, "status": proposal.status},
            evidence={"source": "ontology_proposal", "proposal_id": proposal.id},
        )


ontology_status_tool = OntologyStatusTool()
knowledge_withdraw_tool = KnowledgeWithdrawTool()
