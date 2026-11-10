"""Business Event Hub: one signal and incident identity for every News source.

Newsroom Stories and monitor NewsItems keep their own contracts. Each becomes a
``BusinessSignal`` that records only routing metadata: source reference and
digest, semantic metric, the approved concept it is bound to, a fingerprint of
the population (never its filter values), window, direction and severity.
Measured values, segment names and narrative stay in the source, which is
proven again for every reader.

Signals of the same population, with overlapping windows, whose concepts are
the same or linked by an approved ontology relation, form one
``BusinessIncident``. Grouping is correlation only: an incident never asserts
cause and effect.

Every write is idempotent. Signal ids derive from the source and its proof
digest, an incident id from the signal that opened it, and the newsroom
records the signals of a whole edition on every cycle, so a cycle interrupted
after its stories were saved is healed by the next one.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import Field

from app.common.audit import write_audit_log
from app.core.database import db
from app.modules.agents.semantic.planning import SemanticFilter, SemanticPlan
from app.modules.intelligence.contracts import (
    Contract,
    SemanticRef,
    Window,
    fingerprint,
    utc_now,
)
from app.modules.intelligence.engine_repository import metadata_lock
from app.modules.intelligence.evidence import semantic_population_fingerprint
from app.modules.intelligence.investigation_contracts import Claim, UpdateSubmission
from app.modules.ontology.binding import MetricBinding, bind_metric, linked
from app.modules.ontology.contracts import Actor, OntologyRecord
from app.modules.ontology.repository import RevisionRepository
from app.modules.ontology.schema import revision_table_ddl

logger = logging.getLogger(__name__)

EVENT_TABLES = {
    "signals": "CONFIG_INTELLIGENCE_SIGNALS",
    "incidents": "CONFIG_INTELLIGENCE_INCIDENTS",
    "updates": "CONFIG_INTELLIGENCE_INVESTIGATION_UPDATES",
}
EVENT_DDL = tuple(revision_table_ddl(name) for name in EVENT_TABLES.values())
UNAVAILABLE = "Incident unavailable"
MAX_SIGNALS = 100
OPEN_LIMIT = 50

Severity = Literal["info", "warning", "critical"]
IncidentStatus = Literal[
    "OPEN",
    "TRIAGED",
    "INVESTIGATING",
    "DECISION_PENDING",
    "ACTION_IN_PROGRESS",
    "OBSERVING",
    "RESOLVED",
    "DISMISSED",
]
CLOSED: frozenset[str] = frozenset({"RESOLVED", "DISMISSED"})
TRANSITIONS: dict[str, frozenset[str]] = {
    "OPEN": frozenset({"TRIAGED", "INVESTIGATING", "RESOLVED", "DISMISSED"}),
    "TRIAGED": frozenset({"INVESTIGATING", "RESOLVED", "DISMISSED"}),
    "INVESTIGATING": frozenset({"DECISION_PENDING", "RESOLVED", "DISMISSED"}),
    "DECISION_PENDING": frozenset({"ACTION_IN_PROGRESS", "INVESTIGATING", "RESOLVED"}),
    "ACTION_IN_PROGRESS": frozenset({"OBSERVING", "RESOLVED"}),
    "OBSERVING": frozenset({"RESOLVED", "INVESTIGATING"}),
    "RESOLVED": frozenset({"OPEN"}),
    "DISMISSED": frozenset({"OPEN"}),
}
_SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}


async def ensure_event_schema() -> None:
    for ddl in EVENT_DDL:
        await db.execute_system(ddl)


# --- contracts -----------------------------------------------------------------


class SignalSource(Contract):
    type: Literal["NEWSROOM_STORY", "MONITOR_NEWS_ITEM"]
    id: str = Field(min_length=1, max_length=64)
    revision: int = Field(ge=1)
    digest: str = Field(min_length=64, max_length=64)


class SignalConcept(Contract):
    release_id: str = Field(min_length=1, max_length=64)
    concept_id: str = Field(min_length=1, max_length=64)
    concept_revision: int = Field(ge=1)
    representation_id: str = Field(min_length=1, max_length=64)


class BusinessSignal(OntologyRecord):
    """A normalized observation; never a cause and never enterprise truth by itself."""

    source: SignalSource
    semantic: SemanticRef
    metric: str = Field(min_length=1, max_length=128)
    binding: Literal["BOUND", "ONTOLOGY_UNBOUND", "NO_RELEASE"]
    concept: SignalConcept | None = None
    population_fingerprint: str = Field(min_length=64, max_length=64)
    window: Window
    direction: Literal["increase", "decrease"]
    severity: Severity
    detected_at: datetime
    incident_id: str | None = Field(default=None, max_length=64)
    status: Literal["DETECTED", "CORRELATED"] = "DETECTED"


class Grouping(Contract):
    """Why a signal joined its incident, as a rule rather than prose."""

    signal_id: str = Field(min_length=1, max_length=64)
    rule: Literal["opened", "same_concept", "related_concepts", "same_metric"]
    relation_id: str | None = Field(default=None, max_length=64)
    with_signal_id: str | None = Field(default=None, max_length=64)


class Transition(Contract):
    from_status: IncidentStatus
    to_status: IncidentStatus
    principal: str = Field(min_length=1, max_length=128)
    active_role: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=2000)
    operation_id: str = Field(min_length=8, max_length=64)
    at: datetime = Field(default_factory=utc_now)


class BusinessIncident(OntologyRecord):
    status: IncidentStatus = "OPEN"
    population_fingerprint: str = Field(min_length=64, max_length=64)
    window: Window
    signal_ids: list[str] = Field(min_length=1, max_length=MAX_SIGNALS)
    concept_ids: list[str] = Field(default_factory=list, max_length=32)
    severity: Severity
    grouping: list[Grouping] = Field(min_length=1, max_length=MAX_SIGNALS)
    ontology_release_id: str | None = Field(default=None, max_length=64)
    #: Grouped signals are related observations, never established causes.
    causal_status: Literal["unknown"] = "unknown"
    transitions: list[Transition] = Field(default_factory=list, max_length=200)


class EvidenceDigest(Contract):
    id: str = Field(min_length=1, max_length=128)
    digest: str = Field(min_length=64, max_length=64)


class UpdateReview(Contract):
    principal: str = Field(min_length=1, max_length=128)
    active_role: str = Field(min_length=1, max_length=128)
    decision: Literal["APPROVE", "REJECT"]
    rationale: str = Field(min_length=1, max_length=2000)
    operation_id: str = Field(min_length=8, max_length=64)
    at: datetime = Field(default_factory=utc_now)


class InvestigationUpdate(OntologyRecord):
    """An evidence-backed finding posted to an incident; shown only after review.

    It is shared only with readers in the author's active role who can prove
    every signal the author could see, because the figures behind it were
    computed under that role's data policy.
    """

    incident_id: str = Field(min_length=1, max_length=64)
    mission_id: str | None = Field(default=None, max_length=64)
    author: str = Field(min_length=1, max_length=128)
    author_role: str = Field(min_length=1, max_length=128)
    summary: str = Field(min_length=1, max_length=2000)
    claims: list[Claim] = Field(min_length=1, max_length=10)
    evidence: list[EvidenceDigest] = Field(min_length=1, max_length=40)
    source_signal_ids: list[str] = Field(min_length=1, max_length=MAX_SIGNALS)
    status: Literal["PENDING_REVIEW", "APPROVED", "REJECTED"] = "PENDING_REVIEW"
    review: UpdateReview | None = None
    operation_id: str = Field(min_length=8, max_length=64)


class UpdateReviewRequest(Contract):
    decision: Literal["APPROVE", "REJECT"]
    rationale: str = Field(min_length=1, max_length=2000)
    operation_id: str = Field(min_length=8, max_length=64)


class TransitionRequest(Contract):
    to: IncidentStatus
    reason: str = Field(min_length=1, max_length=2000)
    expected_revision: int = Field(ge=1)
    operation_id: str = Field(min_length=8, max_length=64)


# --- helpers -------------------------------------------------------------------


def story_population(slice_: Any) -> str:
    """The population of a story, comparable with a monitor plan's population."""
    filters: tuple[SemanticFilter, ...] = ()
    if slice_ is not None:
        filters = (SemanticFilter(field=slice_.dimension, operator="=", value=slice_.value),)
    return semantic_population_fingerprint(SemanticPlan(filters=filters))


def windows_overlap(left: Window, right: Window) -> bool:
    """Overlap of at least half of the shorter window."""
    overlap = (min(left.end, right.end) - max(left.start, right.start)).total_seconds()
    shorter = min(
        (left.end - left.start).total_seconds(), (right.end - right.start).total_seconds()
    )
    return overlap > 0 and overlap * 2 >= shorter


def _union(left: Window, right: Window) -> Window:
    return Window(start=min(left.start, right.start), end=max(left.end, right.end))


def _story_source(story: Any) -> SignalSource:
    return SignalSource(
        type="NEWSROOM_STORY",
        id=story.id,
        revision=story.revision,
        digest=fingerprint([proof.digest for proof in story.proofs]),
    )


def _signal_id(source: SignalSource) -> str:
    return "sig_" + fingerprint([source.type, source.id, source.digest])[:32]


def _concept(binding: MetricBinding) -> SignalConcept | None:
    if binding.status != "BOUND":
        return None
    return SignalConcept(
        release_id=binding.release_id or "",
        concept_id=binding.concept_id or "",
        concept_revision=binding.concept_revision or 1,
        representation_id=binding.representation_id or "",
    )


StoryReader = Callable[[str, dict], Awaitable[Any]]
NewsReader = Callable[[str, dict], Awaitable[Any]]
Binder = Callable[[SemanticRef, str, dict], Awaitable[MetricBinding]]
Linker = Callable[[str, str], Awaitable[str | None]]
DefinitionReader = Callable[[SemanticRef], Awaitable[dict]]


async def _read_story(story_id: str, user: dict):
    from app.modules.intelligence.newsroom import newsroom_service

    return await newsroom_service.readable(story_id, user)


async def _read_news(news_id: str, user: dict):
    from app.modules.intelligence.engine import intelligence_service

    try:
        return await intelligence_service.get("news", news_id, user)
    except HTTPException:
        return None


async def _definition(semantic: SemanticRef) -> dict:
    from app.modules.intelligence.semantic_views import semantic_view_service

    row = await semantic_view_service._version(semantic.view_id, semantic.version)
    if not row or row["fingerprint"] != semantic.fingerprint:
        return {}
    return row["definition"]


async def _visible_concepts(concept_ids: set[str], user: dict) -> dict[str, dict]:
    """Names of the concepts the reader's role may see; others stay anonymous."""
    if not concept_ids:
        return {}
    from app.modules.ontology.service import ontology_service

    snapshot = await ontology_service.snapshot("working")
    # Unreleased work is named only to its stewards; others see the released
    # name, and a concept never released stays anonymous.
    visible = ontology_service.visible_in(snapshot, "working", user)
    released = await ontology_service.snapshot("current")
    for concept_id, concept in ontology_service.visible_concepts(released, user).items():
        visible.setdefault(concept_id, concept)
    return {
        concept_id: {
            "id": concept_id,
            "qualified_name": ontology_service.concept_view(
                visible[concept_id], snapshot.domains
            )["qualified_name"],
            "label": visible[concept_id].label,
        }
        for concept_id in concept_ids
        if concept_id in visible
    }


class BusinessEventService:
    def __init__(
        self,
        repository: RevisionRepository | None = None,
        *,
        read_story: StoryReader = _read_story,
        read_news: NewsReader = _read_news,
        bind: Binder = bind_metric,
        link: Linker = linked,
        definition: DefinitionReader = _definition,
        concept_names: Callable[[set[str], dict], Awaitable[dict[str, dict]]] = _visible_concepts,
    ) -> None:
        self.repository = repository or RevisionRepository(EVENT_TABLES, "business-events")
        self.read_story = read_story
        self.read_news = read_news
        self.bind = bind
        self.link = link
        self.definition = definition
        self.concept_names = concept_names

    # --- ingestion -----------------------------------------------------------------

    async def record_stories(self, stories: list, actor: Actor) -> list[BusinessSignal]:
        """Record and correlate a signal for every story of one edition."""
        definitions: dict[str, dict] = {}
        recorded = []
        for story in stories:
            key = story.semantic.model_dump_json()
            if key not in definitions:
                definitions[key] = await self.definition(story.semantic)
            source = _story_source(story)
            recorded.append(
                await self._record(
                    source,
                    story.semantic,
                    story.metric,
                    definitions[key],
                    population=story_population(story.slice),
                    window=story.window,
                    change=story.change,
                    severity=story.severity,
                    actor=actor,
                )
            )
        return recorded

    async def record_news_item(self, news, monitor, actor: Actor) -> BusinessSignal | None:
        """Record and correlate the signal of one monitor detection."""
        metrics = (monitor.plan or {}).get("metrics") or []
        if not metrics:
            return None
        try:
            population = semantic_population_fingerprint(SemanticPlan.from_dict(monitor.plan))
        except (TypeError, ValueError):
            return None
        source = SignalSource(
            type="MONITOR_NEWS_ITEM",
            id=news.id,
            revision=news.revision,
            digest=fingerprint([item.digest for item in news.evidence]),
        )
        return await self._record(
            source,
            news.semantic,
            str(metrics[0]),
            await self.definition(news.semantic),
            population=population,
            window=news.window,
            change=news.change,
            severity=news.severity,
            actor=actor,
        )

    async def _record(
        self,
        source: SignalSource,
        semantic: SemanticRef,
        metric: str,
        definition: dict,
        *,
        population: str,
        window: Window,
        change: float,
        severity: Severity,
        actor: Actor,
    ) -> BusinessSignal:
        signal_id = _signal_id(source)
        existing = await self.repository.latest("signals", signal_id, BusinessSignal)
        if existing is None:
            binding = await self.bind(semantic, metric, definition)
            existing = await self.repository.save(
                "signals",
                BusinessSignal(
                    id=signal_id,
                    actor=actor,
                    source=source,
                    semantic=semantic,
                    metric=metric,
                    binding=binding.status,
                    concept=_concept(binding),
                    population_fingerprint=population,
                    window=window,
                    direction="increase" if change > 0 else "decrease",
                    severity=severity,
                    detected_at=utc_now(),
                ),
            )
        if existing.status == "CORRELATED" and existing.incident_id:
            return existing
        return await self._correlate(existing, actor)

    # --- correlation ---------------------------------------------------------------

    async def _correlate(self, signal: BusinessSignal, actor: Actor) -> BusinessSignal:
        async with metadata_lock(f"incident-correlation:{signal.population_fingerprint}"):
            candidates = await self.repository.current_matching(
                "incidents",
                BusinessIncident,
                "population_fingerprint",
                signal.population_fingerprint,
            )
            incident = next(
                (item for item in candidates if signal.id in item.signal_ids), None
            )
            if incident is None:
                incident = await self._join_or_open(signal, candidates, actor)
        if signal.incident_id == incident.id and signal.status == "CORRELATED":
            return signal
        return await self.repository.save(
            "signals",
            signal.model_copy(
                update={"actor": actor, "incident_id": incident.id, "status": "CORRELATED"}
            ),
            expected_revision=signal.revision,
        )

    async def _join_or_open(
        self, signal: BusinessSignal, candidates: list[BusinessIncident], actor: Actor
    ) -> BusinessIncident:
        for incident in sorted(candidates, key=lambda item: item.created_at):
            if incident.status in CLOSED or len(incident.signal_ids) >= MAX_SIGNALS:
                continue
            if not windows_overlap(incident.window, signal.window):
                continue
            grouping = await self._grouping(signal, incident)
            if grouping is None:
                continue
            concept_ids = list(incident.concept_ids)
            if signal.concept and signal.concept.concept_id not in concept_ids:
                concept_ids.append(signal.concept.concept_id)
            return await self.repository.save(
                "incidents",
                incident.model_copy(
                    update={
                        "actor": actor,
                        "signal_ids": [*incident.signal_ids, signal.id],
                        "concept_ids": concept_ids[:32],
                        "grouping": [*incident.grouping, grouping],
                        "window": _union(incident.window, signal.window),
                        "severity": max(
                            incident.severity, signal.severity, key=_SEVERITY_RANK.__getitem__
                        ),
                    }
                ),
                expected_revision=incident.revision,
            )
        return await self.repository.save(
            "incidents",
            BusinessIncident(
                id="inc_" + fingerprint([signal.population_fingerprint, signal.id])[:32],
                actor=actor,
                population_fingerprint=signal.population_fingerprint,
                window=signal.window,
                signal_ids=[signal.id],
                concept_ids=[signal.concept.concept_id] if signal.concept else [],
                severity=signal.severity,
                grouping=[Grouping(signal_id=signal.id, rule="opened")],
                ontology_release_id=signal.concept.release_id if signal.concept else None,
            ),
        )

    async def _grouping(
        self, signal: BusinessSignal, incident: BusinessIncident
    ) -> Grouping | None:
        """The approved reason ``signal`` belongs to ``incident``, if there is one."""
        for member_id in incident.signal_ids:
            member = await self.repository.latest("signals", member_id, BusinessSignal)
            if member is None:
                continue
            if signal.concept and member.concept:
                if signal.concept.concept_id == member.concept.concept_id:
                    return Grouping(
                        signal_id=signal.id, rule="same_concept", with_signal_id=member.id
                    )
                relation = await self.link(signal.concept.concept_id, member.concept.concept_id)
                if relation:
                    return Grouping(
                        signal_id=signal.id,
                        rule="related_concepts",
                        relation_id=relation,
                        with_signal_id=member.id,
                    )
            elif (member.semantic.view_id, member.metric) == (
                signal.semantic.view_id,
                signal.metric,
            ):
                return Grouping(signal_id=signal.id, rule="same_metric", with_signal_id=member.id)
        return None

    # --- reading -------------------------------------------------------------------

    async def _visible_signals(
        self, incident: BusinessIncident, user: dict
    ) -> list[BusinessSignal]:
        visible = []
        for signal_id in incident.signal_ids:
            signal = await self.repository.latest("signals", signal_id, BusinessSignal)
            if signal is None:
                continue
            reader = (
                self.read_story if signal.source.type == "NEWSROOM_STORY" else self.read_news
            )
            if await reader(signal.source.id, user) is not None:
                visible.append(signal)
        return visible

    async def _projection(
        self, incident: BusinessIncident, signals: list[BusinessSignal], user: dict
    ) -> dict:
        ids = {signal.id for signal in signals}
        names = await self.concept_names(
            {signal.concept.concept_id for signal in signals if signal.concept}, user
        )
        window = signals[0].window
        for signal in signals[1:]:
            window = _union(window, signal.window)
        return {
            "id": incident.id,
            "revision": incident.revision,
            "status": incident.status,
            "severity": max(
                (signal.severity for signal in signals), key=_SEVERITY_RANK.__getitem__
            ),
            "window": window.model_dump(mode="json"),
            "causal_status": incident.causal_status,
            "note": "Grouped signals are related observations. No cause has been established.",
            "signals": [
                {
                    "id": signal.id,
                    "source": {"type": signal.source.type, "id": signal.source.id},
                    "semantic_view_id": signal.semantic.view_id,
                    "metric": signal.metric,
                    "concept": names.get(signal.concept.concept_id) if signal.concept else None,
                    "binding": signal.binding,
                    "direction": signal.direction,
                    "severity": signal.severity,
                    "window": signal.window.model_dump(mode="json"),
                }
                for signal in signals
            ],
            "grouping": [
                item.model_dump(mode="json")
                for item in incident.grouping
                if item.signal_id in ids and (item.with_signal_id in ids or item.rule == "opened")
            ],
            "transitions": [item.model_dump(mode="json") for item in incident.transitions],
            "updates": await self._visible_updates(incident, signals, user),
            "created_at": incident.created_at,
            "updated_at": incident.updated_at,
        }

    async def _visible_updates(
        self, incident: BusinessIncident, signals: list[BusinessSignal], user: dict
    ) -> list[dict]:
        """Approved updates this reader may see, and pending ones they may review."""
        actor = Actor.from_user(user)
        provable = {signal.id for signal in signals}
        shown = []
        for update in await self.repository.current_matching(
            "updates", InvestigationUpdate, "incident_id", incident.id, limit=100
        ):
            if update.author_role != actor.active_role:
                continue
            if not set(update.source_signal_ids) <= provable:
                continue
            mine = update.author == actor.principal
            if update.status in {"APPROVED", "PENDING_REVIEW"}:
                shown.append(
                    {
                        "id": update.id,
                        "revision": update.revision,
                        "status": update.status,
                        "summary": update.summary,
                        "claims": [claim.model_dump(mode="json") for claim in update.claims],
                        "author": update.author,
                        "mission_id": update.mission_id if mine else None,
                        "can_review": update.status == "PENDING_REVIEW" and not mine,
                        "review": update.review.model_dump(mode="json") if update.review else None,
                        "created_at": update.created_at,
                    }
                )
        return sorted(shown, key=lambda item: item["created_at"])

    async def incident_for_story(self, story_id: str, user: dict) -> dict | None:
        """The incident of a story the reader can read now, if it has one."""
        story = await self.read_story(story_id, user)
        if story is None:
            await self._audit(user, "READ_STORY_INCIDENT", story_id, "DENIED", "DENY")
            raise HTTPException(status_code=404, detail=UNAVAILABLE)
        signal = await self.repository.latest(
            "signals", _signal_id(_story_source(story)), BusinessSignal
        )
        if signal is None or not signal.incident_id:
            return None
        return await self.get(signal.incident_id, user)

    async def submit_update(
        self,
        incident_id: str,
        body: UpdateSubmission,
        user: dict,
        *,
        evidence: dict[str, str],
        operation_id: str,
        mission_id: str | None = None,
    ) -> InvestigationUpdate:
        """Post a finding that cites only evidence verified in the caller's own run."""
        incident = await self.repository.latest("incidents", incident_id, BusinessIncident)
        signals = await self._visible_signals(incident, user) if incident else []
        if incident is None or not signals:
            raise HTTPException(status_code=404, detail=UNAVAILABLE)
        cited = {
            ref
            for claim in body.claims
            for ref in (*claim.supporting_evidence_ids, *claim.contradicting_evidence_ids)
        }
        unknown = sorted(cited - set(evidence))
        if unknown:
            await self._audit(user, "POST_INVESTIGATION_UPDATE", incident.id, "DENIED", "DENY")
            raise HTTPException(
                status_code=422,
                detail="These evidence ids are not verified results of this run: "
                + ", ".join(unknown[:10]),
            )
        actor = Actor.from_user(user)
        update = InvestigationUpdate(
            id="upd_" + fingerprint([incident.id, actor.principal, operation_id])[:32],
            actor=actor,
            incident_id=incident.id,
            mission_id=mission_id,
            author=actor.principal,
            author_role=actor.active_role,
            summary=body.summary,
            claims=body.claims,
            evidence=[EvidenceDigest(id=ref, digest=evidence[ref]) for ref in sorted(cited)],
            source_signal_ids=[signal.id for signal in signals],
            operation_id=operation_id,
        )
        saved = await self.repository.save("updates", update)
        await self._audit(user, "POST_INVESTIGATION_UPDATE", incident.id, "SUCCESS", "PENDING")
        return saved

    async def review_update(
        self, incident_id: str, update_id: str, body: UpdateReviewRequest, user: dict
    ) -> dict:
        update = await self.repository.latest("updates", update_id, InvestigationUpdate)
        incident = await self.repository.latest("incidents", incident_id, BusinessIncident)
        signals = await self._visible_signals(incident, user) if incident else []
        actor = Actor.from_user(user)
        if (
            update is None
            or incident is None
            or update.incident_id != incident.id
            or update.author_role != actor.active_role
            or not set(update.source_signal_ids) <= {signal.id for signal in signals}
        ):
            raise HTTPException(status_code=404, detail=UNAVAILABLE)
        if update.review and update.review.operation_id == body.operation_id:
            return await self.get(incident.id, user)
        if update.author == actor.principal:
            await self._audit(user, "REVIEW_INVESTIGATION_UPDATE", update.id, "DENIED", "DENY")
            raise HTTPException(status_code=403, detail="The author cannot review their own update")
        if update.status != "PENDING_REVIEW":
            raise HTTPException(status_code=409, detail="This update was already reviewed")
        await self.repository.save(
            "updates",
            update.model_copy(
                update={
                    "actor": actor,
                    "status": "APPROVED" if body.decision == "APPROVE" else "REJECTED",
                    "review": UpdateReview(
                        principal=actor.principal,
                        active_role=actor.active_role,
                        decision=body.decision,
                        rationale=body.rationale,
                        operation_id=body.operation_id,
                    ),
                }
            ),
            expected_revision=update.revision,
        )
        await self._audit(user, "REVIEW_INVESTIGATION_UPDATE", update.id, "SUCCESS", body.decision)
        return await self.get(incident.id, user)

    async def get(self, incident_id: str, user: dict) -> dict:
        incident = await self.repository.latest("incidents", incident_id, BusinessIncident)
        signals = await self._visible_signals(incident, user) if incident else []
        if incident is None or not signals:
            await self._audit(user, "READ_INCIDENT", incident_id, "DENIED", "DENY")
            raise HTTPException(status_code=404, detail=UNAVAILABLE)
        await self._audit(user, "READ_INCIDENT", incident.id, "SUCCESS", "ALLOW")
        return await self._projection(incident, signals, user)

    async def list(self, user: dict, *, include_closed: bool = False) -> list[dict]:
        """The most recent incidents the reader can see at least one signal of."""
        incidents = await self.repository.current("incidents", BusinessIncident)
        recent = sorted(
            (item for item in incidents if include_closed or item.status not in CLOSED),
            key=lambda item: item.updated_at,
            reverse=True,
        )[:OPEN_LIMIT]
        shown = []
        for incident in recent:
            signals = await self._visible_signals(incident, user)
            if signals:
                shown.append(await self._projection(incident, signals, user))
        await self._audit(user, "READ_INCIDENTS", "incidents", "SUCCESS", "ALLOW")
        return shown

    # --- lifecycle -----------------------------------------------------------------

    async def transition(self, incident_id: str, body: TransitionRequest, user: dict) -> dict:
        incident = await self.repository.latest("incidents", incident_id, BusinessIncident)
        if incident is None or not await self._visible_signals(incident, user):
            raise HTTPException(status_code=404, detail=UNAVAILABLE)
        if any(item.operation_id == body.operation_id for item in incident.transitions):
            return await self.get(incident.id, user)
        if incident.revision != body.expected_revision:
            raise HTTPException(status_code=409, detail="Incident changed; reload before editing")
        if body.to not in TRANSITIONS[incident.status]:
            await self._audit(user, "TRANSITION_INCIDENT", incident.id, "DENIED", "DENY")
            raise HTTPException(
                status_code=422,
                detail=f"An incident cannot move from {incident.status} to {body.to}",
            )
        actor = Actor.from_user(user)
        await self.repository.save(
            "incidents",
            incident.model_copy(
                update={
                    "actor": actor,
                    "status": body.to,
                    "transitions": [
                        *incident.transitions,
                        Transition(
                            from_status=incident.status,
                            to_status=body.to,
                            principal=actor.principal,
                            active_role=actor.active_role,
                            reason=body.reason,
                            operation_id=body.operation_id,
                        ),
                    ][-200:],
                }
            ),
            expected_revision=incident.revision,
        )
        await self._audit(user, "TRANSITION_INCIDENT", incident.id, "SUCCESS", body.to)
        return await self.get(incident.id, user)

    async def audit_refusal(self, user: dict, action: str, name: str) -> None:
        """Record a refused request that never reached the service."""
        await self._audit(user, action, name, "DENIED", "DENY")

    @staticmethod
    async def _audit(user: dict, action: str, name: str, status: str, decision: str) -> None:
        await write_audit_log(
            event_type="INTELLIGENCE",
            user_name=user.get("username") or "unknown",
            action=action,
            object_type="BusinessIncident",
            object_name=name[:64],
            status=status,
            session_id=user.get("session_id"),
            active_role=user.get("active_role"),
            security_context_version=user.get("security_context_version"),
            decision=decision[:32],
        )


business_event_service = BusinessEventService()


#: Starting weights of the business significance score. They are hypotheses to
#: calibrate against reviewed News labels, not learned values: a story about an
#: approved goal outranks a similar move, and corroboration outranks a lone move.
SIGNIFICANCE_WEIGHTS = {"goal": 0.75, "corroboration": 0.5, "official": 0.25}


async def significance(stories: list, user: dict, *, service: BusinessEventService | None = None,
                       ontology=None) -> dict[str, dict]:
    """Deterministic significance of the stories one reader can see now.

    Every feature reads only what this reader already sees: other stories in
    the same list, and ontology concepts their role may see. So the order
    cannot reveal hidden signals or concepts.
    """
    from app.modules.ontology.service import ontology_service as default_ontology

    service = service or business_event_service
    ontology = ontology or default_ontology
    ids = {story.id: _signal_id(_story_source(story)) for story in stories}
    signals = await service.repository.latest_many(
        "signals", list(ids.values()), BusinessSignal
    )
    snapshot = await ontology.snapshot("current")
    visible = ontology.visible_concepts(snapshot, user) if snapshot.release else {}

    def name(concept_id: str) -> str:
        return ontology.concept_view(visible[concept_id], snapshot.domains)["qualified_name"]

    goals: dict[str, list[str]] = {}
    for relation in snapshot.relations:
        if (
            relation.status == "ACTIVE"
            and relation.type.value == "AFFECTS_GOAL"
            and relation.target_id in visible
        ):
            goals.setdefault(relation.source_id, []).append(relation.target_id)
    incidents: dict[str, int] = {}
    for known in signals.values():
        if known.incident_id:
            incidents[known.incident_id] = incidents.get(known.incident_id, 0) + 1

    scored = {}
    for story_id, signal_id in ids.items():
        signal = signals.get(signal_id)
        boost, reasons = 0.0, []
        concept = signal.concept.concept_id if signal and signal.concept else None
        if concept and goals.get(concept):
            boost += SIGNIFICANCE_WEIGHTS["goal"]
            reasons.append("Affects goal " + ", ".join(sorted(name(g) for g in goals[concept])))
        others = incidents.get(signal.incident_id, 0) - 1 if signal and signal.incident_id else 0
        if others > 0:
            boost += SIGNIFICANCE_WEIGHTS["corroboration"]
            reasons.append(
                f"Moved with {others} related {'story' if others == 1 else 'stories'} here"
            )
        if concept and concept in visible:
            boost += SIGNIFICANCE_WEIGHTS["official"]
            reasons.append(f"Official definition: {name(concept)}")
        scored[story_id] = {"boost": round(boost, 4), "reasons": reasons}
    return scored


async def record_stories_safely(stories: list, user: dict) -> None:
    """Called by the newsroom after an edition is saved; never fails the edition."""
    try:
        await business_event_service.record_stories(stories, Actor.from_user(user))
    except Exception as exc:  # noqa: BLE001 - the next cycle records the same signals
        logger.warning("Business signals were not recorded: %s", type(exc).__name__)


async def record_news_item_safely(news, monitor, user: dict) -> None:
    """Called after a monitor detection is saved; never fails the detection."""
    try:
        await business_event_service.record_news_item(news, monitor, Actor.from_user(user))
    except Exception as exc:  # noqa: BLE001 - the next run records the same signal
        logger.warning("Business signal was not recorded: %s", type(exc).__name__)


__all__ = [
    "BusinessIncident",
    "InvestigationUpdate",
    "UpdateReviewRequest",
    "UpdateSubmission",
    "BusinessSignal",
    "TransitionRequest",
    "business_event_service",
    "ensure_event_schema",
    "record_news_item_safely",
    "record_stories_safely",
]
