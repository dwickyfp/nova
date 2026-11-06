"""News incident tools for the bounded Studio engine.

``incident_context`` reads the incident the Mission investigates, proving every
News source again for the caller. ``investigation_update`` posts a finding to
that incident for review; each claim must cite verified result tables of the
current run, and nothing is shown in News until a reviewer approves it.
"""

from __future__ import annotations

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import Field, ValidationError

from app.modules.assistant.schemas import ToolClassification
from app.modules.assistant.tools import ToolOutcome
from app.modules.intelligence.contracts import Contract, fingerprint
from app.modules.intelligence.investigation_contracts import Claim, UpdateSubmission


class IncidentRequest(Contract):
    incident_id: str | None = Field(
        default=None,
        max_length=64,
        description="Omit to read the incident this Mission investigates.",
    )


class UpdateRequest(Contract):
    incident_id: str | None = Field(default=None, max_length=64)
    summary: str = Field(min_length=1, max_length=2000)
    claims: list[Claim] = Field(min_length=1, max_length=10)


def _incident_id(context, requested: str | None) -> str:
    incident_id = requested or getattr(context, "incident_id", None)
    if not incident_id:
        raise ValueError("No incident is attached to this Mission; give an incident_id")
    return incident_id


def _failure(exc: Exception) -> ToolOutcome:
    return ToolOutcome(ok=False, summary="", error=str(getattr(exc, "detail", exc)))


class IncidentContextTool:
    name = "incident_context"
    description = (
        "Read the News incident this Mission investigates: the related signals you can see, "
        "their approved business concepts, windows and grouping rules, its status, and "
        "reviewed investigation updates. Related signals are correlation, never cause."
    )
    parameters = IncidentRequest.model_json_schema()
    classification: ToolClassification = "read_only"
    requires_consent = True

    def preview(self, invocation):
        return "Read the News incident under investigation"

    async def run(self, invocation, context):
        from app.common.news_entitlement import require_news_entitlement
        from app.modules.intelligence.business_events import business_event_service

        try:
            body = IncidentRequest.model_validate(invocation.arguments)
            incident_id = _incident_id(context, body.incident_id)
            await require_news_entitlement(context.user, action="TOOL_READ_INCIDENT")
            incident = await business_event_service.get(incident_id, context.user)
        except (HTTPException, ValueError, ValidationError) as exc:
            return _failure(exc)
        return ToolOutcome(
            ok=True,
            summary=(
                f"Incident {incident['status'].lower()}: {len(incident['signals'])} related "
                "signal(s) you can see. No cause has been established."
            ),
            data=jsonable_encoder(incident),
            evidence={"source": "business_incident", "incident_id": incident_id},
        )


class InvestigationUpdateTool:
    name = "investigation_update"
    description = (
        "Post a finding to the News incident for review. Every claim cites the evidence_id of "
        "a verified result from this turn and states its causal status: arithmetic for an "
        "exact decomposition, association for co-movement, unknown otherwise. Nothing appears "
        "in News until a reviewer in your role approves it."
    )
    parameters = UpdateRequest.model_json_schema()
    #: It writes a reviewable proposal, never a published fact.
    classification: ToolClassification = "session_change"
    requires_consent = True

    def preview(self, invocation):
        return "Post an investigation finding for review"

    async def run(self, invocation, context):
        from app.common.news_entitlement import require_news_entitlement
        from app.modules.intelligence.business_events import business_event_service

        tables = getattr(context, "evidence_tables", None) or {}
        try:
            body = UpdateRequest.model_validate(invocation.arguments)
            incident_id = _incident_id(context, body.incident_id)
            await require_news_entitlement(context.user, action="TOOL_POST_INVESTIGATION")
            update = await business_event_service.submit_update(
                incident_id,
                UpdateSubmission(summary=body.summary, claims=body.claims),
                context.user,
                evidence={
                    str(evidence_id): fingerprint(table) for evidence_id, table in tables.items()
                },
                operation_id=fingerprint(
                    [getattr(context, "run_id", None), invocation.tool_call_id]
                )[:48],
                mission_id=getattr(context, "mission_id", None),
            )
        except (HTTPException, ValueError, ValidationError) as exc:
            outcome = _failure(exc)
            if isinstance(exc, HTTPException) and exc.status_code == 422:
                return ToolOutcome(
                    ok=False,
                    summary="",
                    error=outcome.error,
                    error_class="UNVERIFIED_EVIDENCE",
                    recoverable=True,
                    safe_detail="Cite only evidence_id values of data results from this turn.",
                    repair_context={"evidence_ids": list(tables)[-8:]},
                )
            return outcome
        return ToolOutcome(
            ok=True,
            summary="Finding posted for review; it is not shown in News until approved.",
            data={"update_id": update.id, "status": update.status},
            evidence={"source": "investigation_update", "update_id": update.id},
        )


class DraftInvestigationUpdateTool:
    """Smart's form of ``investigation_update``: it prepares the finding, the user posts it."""

    name = "draft_investigation_update"
    description = (
        "Prepare a finding for the News incident review. Every claim cites the evidence_id of "
        "a verified result from this turn and states its causal status: arithmetic for an "
        "exact decomposition, association for co-movement, unknown otherwise. It posts "
        "nothing: the user submits the draft, and a reviewer in their role approves it "
        "before News shows it. Say it is ready to submit; never say it was posted."
    )
    parameters = UpdateRequest.model_json_schema()
    #: Nothing is written; posting the finding is the user's own action.
    classification: ToolClassification = "read_only"
    requires_consent = False

    def preview(self, invocation):
        return "Prepare an investigation finding for you to submit"

    async def run(self, invocation, context):
        from app.common.news_entitlement import require_news_entitlement
        from app.modules.agents.tools.drafts import add_draft
        from app.modules.intelligence import update_drafts
        from app.modules.intelligence.business_events import business_event_service

        tables = getattr(context, "evidence_tables", None) or {}
        try:
            body = UpdateRequest.model_validate(invocation.arguments)
            incident_id = _incident_id(context, body.incident_id)
            await require_news_entitlement(context.user, action="TOOL_DRAFT_INVESTIGATION")
            await business_event_service.get(incident_id, context.user)
        except (HTTPException, ValueError, ValidationError) as exc:
            return _failure(exc)
        cited = {
            ref
            for claim in body.claims
            for ref in (*claim.supporting_evidence_ids, *claim.contradicting_evidence_ids)
        }
        unknown = sorted(cited - set(tables))
        if unknown:
            return ToolOutcome(
                ok=False,
                summary="",
                error="These evidence ids are not verified results of this run: "
                + ", ".join(unknown[:10]),
                error_class="UNVERIFIED_EVIDENCE",
                recoverable=True,
                safe_detail="Cite only evidence_id values of data results from this turn.",
                repair_context={"evidence_ids": list(tables)[-8:]},
            )
        token = update_drafts.prepare(
            incident_id=incident_id,
            summary=body.summary,
            claims=body.claims,
            evidence={ref: fingerprint(tables[ref]) for ref in sorted(cited)},
            operation_id=fingerprint(
                [getattr(context, "run_id", None), invocation.tool_call_id]
            )[:48],
            mission_id=getattr(context, "mission_id", None),
            user=context.user,
        )
        draft = {
            "draft_kind": "investigation_update",
            "title": "Finding for the incident review",
            "detail": body.summary,
            "incident_id": incident_id,
            "payload": {"draft": token},
        }
        if not add_draft(context, draft):
            return ToolOutcome(ok=False, summary="", error="No more drafts fit in this answer")
        return ToolOutcome(
            ok=True,
            summary="Finding prepared; it is posted for review only when the user submits it.",
            data={"incident_id": incident_id, "posted": False},
        )


incident_context_tool = IncidentContextTool()
investigation_update_tool = InvestigationUpdateTool()
draft_investigation_update_tool = DraftInvestigationUpdateTool()
