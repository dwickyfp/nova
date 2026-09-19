"""Migration apply planner — deterministic, dependency-ordered execution plan.

The planner turns the objects discovered by enumeration + dry-run into an ordered
list of steps that a target can execute top to bottom. It is **pure**: it takes
already-collected definitions and returns a plan; it opens no connections and
executes nothing. The apply path that consumes a plan is gated separately
(issue #7).

Ordering rules (a later step may depend on an earlier one):

1. ``database`` — the target database must exist before anything is created in it.
2. ``table`` — views and MVs reference tables.
3. ``view`` — views may reference tables and other views.
4. ``materialized_view`` — built on top of tables.
5. ``function`` — a view/MV body may call a function; create functions last so
   every referenced object already exists.

Objects whose DDL could not be reconstructed (``skipped`` verdicts with no
definition, non-native UDFs) are excluded from the executable steps and surfaced
as ``blocked`` so the operator sees exactly what a cutover would omit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from app.modules.migration.retarget import (
    Retargeted,
    RetargetError,
    make_idempotent,
    retarget,
)
from app.modules.migration.schemas import MigrationVerdict, ObjectKind


class PlanStepKind(StrEnum):
    """The ordered phases of a migration plan."""

    DATABASE = "database"
    TABLE = "table"
    VIEW = "view"
    MATERIALIZED_VIEW = "materialized_view"
    FUNCTION = "function"
    TASK = "task"


#: The order steps are emitted in. A new kind must be inserted here deliberately.
STEP_ORDER: tuple[PlanStepKind, ...] = (
    PlanStepKind.DATABASE,
    PlanStepKind.TABLE,
    PlanStepKind.VIEW,
    PlanStepKind.MATERIALIZED_VIEW,
    PlanStepKind.FUNCTION,
    PlanStepKind.TASK,
)

#: Map a connector object kind to the step it belongs in.
_STEP_BY_OBJECT_KIND: dict[ObjectKind, PlanStepKind] = {
    ObjectKind.TABLE: PlanStepKind.TABLE,
    ObjectKind.VIEW: PlanStepKind.VIEW,
    ObjectKind.MATERIALIZED_VIEW: PlanStepKind.MATERIALIZED_VIEW,
    ObjectKind.FUNCTION: PlanStepKind.FUNCTION,
    ObjectKind.TASK: PlanStepKind.TASK,
}


@dataclass(frozen=True)
class PlanStep:
    """One executable statement in the plan."""

    order: int
    kind: PlanStepKind
    object_name: str
    statement: str
    dropped_properties: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class BlockedObject:
    """An object that cannot be executed, with the reason the operator must read."""

    name: str
    kind: ObjectKind
    reason: str


@dataclass(frozen=True)
class ApplyPlan:
    """A dependency-ordered plan for one database."""

    source_database: str
    target_database: str
    steps: tuple[PlanStep, ...]
    blocked: tuple[BlockedObject, ...]

    @property
    def step_count(self) -> int:
        return len(self.steps)


#: A candidate for planning: the connector's object plus its collected definition.
@dataclass(frozen=True)
class PlanCandidate:
    name: str
    kind: ObjectKind
    verdict: MigrationVerdict
    ddl: str | None
    scope: str = "database"


def _blocked_reason(candidate: PlanCandidate) -> str:
    if candidate.verdict is MigrationVerdict.SKIPPED:
        return "Skipped by the dry-run: no definition can be produced."
    if candidate.verdict is MigrationVerdict.LOSSY and candidate.ddl:
        return "Lossy: a definition exists but may differ from the source."
    return "No definition was collected, so the object cannot be executed."


def build_plan(
    candidates: list[PlanCandidate],
    *,
    source_database: str,
    target_database: str,
    create_database: bool = True,
    target_replication_num: int | None = None,
) -> ApplyPlan:
    """Build an ordered, executable plan from dry-run candidates.

    A candidate is executable when it has a definition (``ddl``) and its kind
    maps to a step. Everything else is reported in ``blocked`` — a declared
    omission, never a silent drop.

    ``target_replication_num`` is the target cluster's safe replication factor;
    when supplied, table steps carry it so a multi-backend source does not
    produce a table a single-backend target cannot place.
    """
    if not source_database or not target_database:
        raise ValueError("source_database and target_database are required")
    # Same name on both clusters is legitimate (different clusters); the planner
    # still qualifies objects and strips deployment properties.

    grouped: dict[PlanStepKind, list[PlanStep]] = {kind: [] for kind in STEP_ORDER}
    blocked: list[BlockedObject] = []
    next_order = 0

    if create_database:
        grouped[PlanStepKind.DATABASE].append(
            PlanStep(
                order=next_order,
                kind=PlanStepKind.DATABASE,
                object_name=target_database,
                statement=f"CREATE DATABASE IF NOT EXISTS `{target_database}`",
            )
        )
        next_order += 1

    for candidate in candidates:
        step_kind = _STEP_BY_OBJECT_KIND.get(candidate.kind)
        if step_kind is None or not candidate.ddl:
            blocked.append(
                BlockedObject(
                    name=candidate.name,
                    kind=candidate.kind,
                    reason=_blocked_reason(candidate),
                )
            )
            continue
        try:
            retargeted: Retargeted = retarget(
                candidate.ddl,
                kind=candidate.kind.value,
                object_name=candidate.name,
                source_database=source_database,
                target_database=target_database,
                target_replication_num=target_replication_num,
            )
        except RetargetError as exc:
            blocked.append(
                BlockedObject(
                    name=candidate.name,
                    kind=candidate.kind,
                    reason=f"Could not be retargeted for the target: {exc}",
                )
            )
            continue
        grouped[step_kind].append(
            PlanStep(
                order=next_order,
                kind=step_kind,
                object_name=candidate.name,
                # Every object step is guarded so a partially-applied plan can be
                # re-run: a first attempt may have created some objects before a
                # later step failed.
                statement=make_idempotent(retargeted.statement),
                dropped_properties=retargeted.dropped_properties,
            )
        )
        next_order += 1

    steps: list[PlanStep] = []
    for step_kind in STEP_ORDER:
        ordered = sorted(grouped[step_kind], key=lambda s: s.object_name)
        # Re-number in emission order so ``order`` is the true execution sequence.
        for step in ordered:
            steps.append(
                PlanStep(
                    order=len(steps),
                    kind=step.kind,
                    object_name=step.object_name,
                    statement=step.statement,
                    dropped_properties=step.dropped_properties,
                )
            )

    return ApplyPlan(
        source_database=source_database,
        target_database=target_database,
        steps=tuple(steps),
        blocked=tuple(blocked),
    )
