"""Shared News editions over Semantic Views with a per-reader access proof.

Generation is deterministic and runs as the execution account bound to the
view's News role. Reading never trusts that account's visibility: the reader
re-runs every proof query of the edition under their own active role, and a
story exists for them only when each proof digest is reproduced exactly. Row
filters and masks stay enforced by the engine; nothing here evaluates policy.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from statistics import median
from time import monotonic
from types import SimpleNamespace
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import ValidationError

from app.common.audit import write_audit_log
from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.ir import Additivity, SemanticModelIR
from app.modules.agents.semantic.planning import SemanticPlan, SemanticPlanError
from app.modules.intelligence.contracts import (
    Confidence,
    Scope,
    SemanticRef,
    Window,
    fingerprint,
    utc_now,
)
from app.modules.intelligence.engine import CycleBudget, intelligence_service
from app.modules.intelligence.engine_repository import intelligence_repository, metadata_lock
from app.modules.intelligence.newsroom_contracts import (
    BusinessRule,
    Driver,
    Edition,
    Narrative,
    NewsConfig,
    NewsSettings,
    ProofGroup,
    SeriesPoint,
    Story,
    StoryProof,
    StorySlice,
)
from app.modules.intelligence.schedules import configure_schedule
from app.modules.intelligence.semantic_views import semantic_view_service
from app.modules.ml_engine.analysis import detect_change
from app.modules.task_orchestration.access import is_task_admin
from app.modules.task_orchestration.repository import task_orchestration_repository

HANDLER = "intelligence.newsroom"
DETECTION_METHOD = "matched-weekday-median-mad-v1"
#: Roles that administer the platform never publish business news.
_FORBIDDEN_EXECUTION_ROLES = frozenset(
    {
        "ACCOUNTADMIN",
        "SECURITYADMIN",
        "ROOT",
        "PUBLIC",
        "USER_ADMIN",
        "SECURITY_ADMIN",
        "DB_ADMIN",
        "CLUSTER_ADMIN",
    }
)
_MAX_VIEWS = 8
_READER_CONCURRENCY = 4
#: Model drafts attempted per cycle; the rest keep the template until the next one.
_MAX_WRITES = 3
_UNAVAILABLE = "Record unavailable"


def canonical(value: Any) -> Any:
    """A stable form of one result cell, so equal data always hashes equally."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, float):
        value = Decimal(f"{value:.12g}")
    if isinstance(value, int | Decimal):
        number = Decimal(value)
        text = format(number.normalize(), "f") if number else "0"
        return ["n", text]
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


def row_digest(columns: list[str], rows: list[list]) -> str:
    return fingerprint(
        {
            "columns": list(columns),
            "rows": sorted(json.dumps([canonical(cell) for cell in row]) for row in rows),
        }
    )


def _day(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def label(name: str) -> str:
    return name.replace("_", " ").strip().capitalize() or name


def format_number(value: float, unit: str | None = None) -> str:
    text = f"{value:,.0f}" if abs(value) >= 100 else f"{value:,.2f}".rstrip("0").rstrip(".")
    if not unit:
        return text
    # Currency codes lead the amount; every other unit follows it.
    return f"{unit} {text}" if len(unit) == 3 and unit.isupper() else f"{text} {unit}"


def format_percent(relative: float) -> str:
    return f"{abs(relative) * 100:.1f}%"


def group_key(metric: str, dimension: str | None) -> str:
    return fingerprint([metric, dimension])[:16]


def edition_day(config: NewsConfig, now: datetime | None = None) -> date:
    """The last complete local day."""
    clock = (now or utc_now()).astimezone(ZoneInfo(config.timezone))
    return clock.date() - timedelta(days=1)


def daily_plan(config: NewsConfig, metric: str, dimension: str | None, day: date) -> dict:
    metrics = [metric] if metric == config.count_metric else [metric, config.count_metric]
    start = day - timedelta(days=config.days - 1)

    def bound(value: date) -> str:
        return datetime.combine(value, time.min).strftime("%Y-%m-%d %H:%M:%S.%f")

    return {
        "metrics": metrics,
        "dimensions": [dimension] if dimension else [],
        "time": {"dimension": config.time_dimension, "grain": "day"},
        "filters": [
            {"field": config.time_dimension, "operator": ">=", "value": bound(start)},
            {
                "field": config.time_dimension,
                "operator": "<",
                "value": bound(day + timedelta(days=1)),
            },
        ],
        "limit": 1000,
    }


def proof_groups(config: NewsConfig, day: date) -> list[ProofGroup]:
    return [
        ProofGroup(
            key=group_key(metric, dimension),
            metric=metric,
            dimension=dimension,
            plan=daily_plan(config, metric, dimension, day),
        )
        for metric in config.metrics
        for dimension in [None, *config.slice_dimensions]
    ]


@dataclass
class GroupRows:
    """One proof query's rows, split by slice value."""

    columns: list[str]
    rows: list[list]
    by_value: dict[str | None, list[list]] = field(default_factory=dict)
    series: dict[str | None, dict[date, tuple[float, int]]] = field(default_factory=dict)

    def digest(self, value: str | None = None, *, whole: bool = False) -> tuple[str, int]:
        rows = self.rows if whole else self.by_value.get(value, [])
        return row_digest(self.columns, rows), len(rows)


def read_group(group: ProofGroup, config: NewsConfig, result: dict) -> GroupRows | None:
    """Parse a proof result; ``None`` when it cannot serve as a complete proof."""
    if result.get("truncated"):
        return None
    columns = list(result["columns"])
    known = {group.metric, config.count_metric, group.dimension}
    time_columns = [name for name in columns if name not in known]
    if (
        len(time_columns) != 1
        or group.metric not in columns
        or config.count_metric not in columns
        or (group.dimension is not None and group.dimension not in columns)
    ):
        return None
    index = {name: columns.index(name) for name in columns}
    parsed = GroupRows(columns=columns, rows=[list(row) for row in result["rows"]])
    for row in parsed.rows:
        value = None
        if group.dimension is not None:
            raw = row[index[group.dimension]]
            if raw is None:
                continue
            value = str(raw)
        amount, count = row[index[group.metric]], row[index[config.count_metric]]
        if amount is None or count is None:
            continue
        parsed.by_value.setdefault(value, []).append(row)
        parsed.series.setdefault(value, {})[_day(row[index[time_columns[0]]])] = (
            float(amount),
            int(count),
        )
    return parsed


@dataclass
class Candidate:
    group: ProofGroup
    value: str | None
    detection: dict
    rows: GroupRows
    baseline_dates: list[date]


def detect_slice(
    series: dict[date, tuple[float, int]], config: NewsConfig, day: date
) -> tuple[dict, list[date]] | None:
    current = series.get(day)
    if current is None:
        return None
    baseline_dates = [day - timedelta(weeks=week) for week in range(1, config.baseline_weeks + 1)]
    baselines = [series.get(item, (0.0, 0)) for item in baseline_dates]
    detection = detect_change(
        [value for value, _ in baselines],
        current[0],
        sample_count=min([current[1], *(count for _, count in baselines)]),
        minimum_samples=config.minimum_samples,
        relative_threshold=config.relative_threshold,
        absolute_threshold=config.absolute_threshold,
    )
    return detection, baseline_dates


def rank_candidates(candidates: list[Candidate], limit: int) -> list[Candidate]:
    """Order by severity, totals first, then size; drop a total one slice explains."""
    kept = []
    for candidate in candidates:
        if candidate.value is None and any(
            other.value is not None
            and other.group.metric == candidate.group.metric
            and (other.detection["change"] > 0) == (candidate.detection["change"] > 0)
            and abs(other.detection["change"]) >= 0.8 * abs(candidate.detection["change"])
            for other in candidates
        ):
            continue
        kept.append(candidate)
    kept.sort(
        key=lambda item: (
            item.detection["severity"] != "critical",
            item.value is not None,
            -abs(item.detection["relative_change"] or 0),
            -abs(item.detection["change"]),
            item.group.metric,
            item.group.dimension or "",
            item.value or "",
        )
    )
    return kept[:limit]


def story_slots(story: Story, config: NewsConfig) -> dict[str, str]:
    """Every number a narrative may state, already formatted from proven rows."""
    weekday = story.edition_date.strftime("%A")
    return {
        "metric": story.metric_label,
        "dimension": label(story.slice.dimension).lower() if story.slice else "",
        "slice": story.slice.value if story.slice else "",
        "after": format_number(story.after, story.unit),
        "before": format_number(story.before, story.unit),
        "change": format_number(abs(story.change), story.unit),
        "change_pct": format_percent(story.relative_change)
        if story.relative_change is not None
        else "",
        "direction": "rose" if story.change > 0 else "fell",
        "date": f"{story.edition_date.strftime('%A')}, {story.edition_date.day} "
        f"{story.edition_date.strftime('%B %Y')}",
        "weekday": weekday,
        "baseline_weeks": str(len(story.baseline_dates)),
        "threshold_pct": format_percent(config.relative_threshold),
    }


def template_narrative(story: Story, config: NewsConfig) -> Narrative:
    slot = story_slots(story, config)
    where = f" in {slot['slice']}" if story.slice else " overall"
    subject = (
        f"{slot['metric']} for {slot['dimension']} {slot['slice']}"
        if story.slice
        else slot["metric"]
    )
    move = (
        f"{slot['direction']} {slot['change_pct']}"
        if slot["change_pct"]
        else f"{slot['direction']} to {slot['after']}"
    )
    relation = "above" if story.change > 0 else "below"
    size = f" ({slot['change_pct']})" if slot["change_pct"] else ""
    check = (
        f"The largest movement is in {label(story.drivers[0].dimension).lower()} "
        f"{story.drivers[0].value}. "
        if story.drivers
        else ""
    )
    return Narrative(
        headline=f"{slot['metric']} {move}{where}"[:160],
        deck=(
            f"{slot['after']} on {slot['date']}, against a typical {slot['weekday']} "
            f"of {slot['before']}."
        )[:320],
        what_happened=(
            f"{subject} came to {slot['after']} on {slot['date']}. The median of the previous "
            f"{slot['baseline_weeks']} {slot['weekday']}s was {slot['before']}, so the day closed "
            f"{slot['change']} {relation} the usual level{size}."
        )[:900],
        why_it_matters=(
            f"The change is larger than the {slot['threshold_pct']} materiality threshold set "
            "for this view and larger than the normal week-to-week variation."
        ),
        what_to_check=(
            f"{check}The cause has not been established. Confirm that the data for "
            f"{slot['date']} is complete before acting on this change."
        )[:900],
    )


def business_rules(ir: SemanticModelIR, story: Story, config: NewsConfig) -> list[BusinessRule]:
    """Business knowledge quoted from the published definition, never invented."""
    rules: list[BusinessRule] = []
    metric = ir.metric(story.metric)
    if metric and metric.description.strip():
        rules.append(
            BusinessRule(
                source="metric", name=story.metric_label, text=metric.description.strip()[:1000]
            )
        )
    if story.slice:
        dimension = ir.field(story.slice.dimension)
        if dimension and dimension.description.strip():
            rules.append(
                BusinessRule(
                    source="dimension",
                    name=label(story.slice.dimension),
                    text=dimension.description.strip()[:1000],
                )
            )
    if ir.description.strip():
        rules.append(BusinessRule(source="view", name=ir.name, text=ir.description.strip()[:1000]))
    rules.append(
        BusinessRule(
            source="threshold",
            name="Materiality",
            text=(
                f"A change is reported when it exceeds {format_percent(config.relative_threshold)} "
                f"of the matched weekday median over {config.baseline_weeks} weeks and at least "
                f"{config.minimum_samples} records support each day."
            ),
        )
    )
    return rules


def public_story(story: Story) -> dict:
    """What a reader receives: no proofs, producer identity or internal keys."""
    return story.model_dump(
        mode="json",
        include={
            "id",
            "revision",
            "metric",
            "metric_label",
            "unit",
            "slice",
            "edition_date",
            "window",
            "baseline_dates",
            "before",
            "after",
            "change",
            "relative_change",
            "severity",
            "rank",
            "series",
            "drivers",
            "narrative",
            "business_rules",
            "narrative_source",
        },
    ) | {
        "view_id": story.semantic.view_id,
        "semantic_version": story.semantic.version,
        "confidence": story.confidence.label,
    }


def validate_config(ir: SemanticModelIR, config: NewsConfig) -> None:
    time_field = ir.field(config.time_dimension)
    if time_field is None or not time_field.is_time:
        raise HTTPException(status_code=422, detail="Choose a time dimension of this view")
    if ir.metric(config.count_metric) is None:
        raise HTTPException(status_code=422, detail="Choose a record count metric of this view")
    for name in config.metrics:
        metric = ir.metric(name)
        if metric is None:
            raise HTTPException(status_code=422, detail=f"Metric {name!r} is not in this view")
        if metric.additivity != Additivity.ADDITIVE:
            raise HTTPException(
                status_code=422, detail=f"News needs an additive metric; {name!r} is not"
            )
    for name in config.slice_dimensions:
        dimension = ir.field(name)
        if dimension is None or dimension.is_time:
            raise HTTPException(
                status_code=422, detail=f"Dimension {name!r} is not available for News"
            )
    for group in proof_groups(config, date(2026, 1, 1)):
        try:
            SemanticCompiler().compile(ir, SemanticPlan.from_dict(group.plan))
        except SemanticPlanError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"News cannot query {group.metric!r} by {group.dimension or 'day'}",
            ) from exc


def default_config(ir: SemanticModelIR, role: str) -> dict:
    """A reviewable starting point; saving still validates every choice."""
    time_field = next(
        (
            ir.field(metric.default_time_dimension)
            for metric in ir.metrics
            if metric.default_time_dimension
        ),
        None,
    ) or next(
        (item for dataset in ir.datasets for item in dataset.fields if item.is_time), None
    )
    additive = [metric for metric in ir.metrics if metric.additivity == Additivity.ADDITIVE]
    counts = [
        metric
        for metric in additive
        if "count" in metric.name.lower() or "count(" in metric.expression.lower().replace(" ", "")
    ]
    dimensions = [
        item.name
        for dataset in ir.datasets
        for item in dataset.fields
        if not item.is_time and 0 < len(item.sample_values) <= 30
    ]
    return {
        "execution_role": role,
        "metrics": [metric.name for metric in additive if metric not in counts][:3]
        or [metric.name for metric in additive][:1],
        "count_metric": counts[0].name if counts else "",
        "slice_dimensions": dimensions[:3],
        "time_dimension": time_field.name if time_field else "",
        "available": {
            "metrics": [metric.name for metric in additive],
            "dimensions": [
                item.name
                for dataset in ir.datasets
                for item in dataset.fields
                if not item.is_time and item.sample_values
            ],
            "time_dimensions": [
                item.name for dataset in ir.datasets for item in dataset.fields if item.is_time
            ],
        },
    }


class NewsroomService:
    def __init__(
        self,
        service=intelligence_service,
        semantic=semantic_view_service,
        repository=intelligence_repository,
        tasks=task_orchestration_repository,
        writer=None,
    ):
        self.service = service
        self.semantic = semantic
        self.repository = repository
        self.tasks = tasks
        self.writer = writer

    # ── Settings ────────────────────────────────────────────────────────────

    @staticmethod
    def _stored_config(view: dict) -> NewsConfig | None:
        raw = view.get("news_config")
        if not raw:
            return None
        try:
            return NewsConfig.model_validate(raw)
        except ValidationError:
            return None

    async def _ir(self, view: dict) -> tuple[SemanticRef, SemanticModelIR]:
        version = view.get("active_version")
        row = await self.semantic._version(view["id"], version) if version else None
        if not row or row["status"] != "ACTIVE":
            raise HTTPException(status_code=409, detail="Publish a version before enabling News")
        ref = SemanticRef(view_id=view["id"], version=version, fingerprint=row["fingerprint"])
        return ref, SemanticModelIR.from_ossie(row["definition"])

    async def defaults(self, view_id: str, user: dict) -> dict:
        view = await self.semantic._owned(view_id, user)
        _, ir = await self._ir(view)
        return default_config(ir, str(user.get("active_role") or ""))

    async def _schedule(self, view_id: str, config: NewsConfig, user: dict, *, enabled: bool):
        scope = Scope(
            principal=config.execution_user,
            active_role=config.execution_role,
            security_context_version=1,
        )
        await configure_schedule(
            SimpleNamespace(id=view_id),
            user,
            handler=HANDLER,
            enabled=enabled,
            cadence=config.cadence_minutes,
            execution_scope=scope,
        )

    async def configure(self, view_id: str, body: NewsSettings, user: dict) -> dict:
        view = await self.semantic._owned(view_id, user)
        previous = self._stored_config(view)
        config = previous
        if body.enabled:
            if body.config is None:
                raise HTTPException(status_code=422, detail="Choose what News should cover")
            role = body.config.execution_role
            if role.upper() in _FORBIDDEN_EXECUTION_ROLES:
                raise HTTPException(
                    status_code=422, detail="Choose a business role, not an administration role"
                )
            if user.get("active_role") != role and not is_task_admin(user):
                raise HTTPException(
                    status_code=403, detail=f"Activate the role {role!r} before enabling News"
                )
            bound = await self.tasks.get_role_execution_user(role)
            if not bound:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Role {role!r} has no scheduled execution account. Ask an administrator "
                        "to provision one, then enable News again."
                    ),
                )
            config = body.config.model_copy(update={"execution_user": bound})
            _, ir = await self._ir(view)
            validate_config(ir, config)
        if previous and previous.execution_user and (
            not body.enabled
            or (previous.execution_user, previous.execution_role)
            != (config.execution_user, config.execution_role)
        ):
            await self._schedule(view_id, previous, user, enabled=False)
        if body.enabled:
            await self._schedule(view_id, config, user, enabled=True)
        await self.semantic.save_news(
            view_id,
            enabled=body.enabled,
            config=config.model_dump(mode="json") if config else None,
            username=user["username"],
        )
        await write_audit_log(
            event_type="SEMANTIC_VIEW",
            user_name=user["username"],
            action="SET_NEWS",
            object_type="SEMANTIC_VIEW",
            object_name=view["name"],
            status="SUCCESS",
            session_id=user.get("session_id"),
            active_role=user.get("active_role"),
            security_context_version=user.get("security_context_version"),
            decision="ENABLE" if body.enabled else "DISABLE",
        )
        return await self.semantic.describe(view_id, user)

    # ── Generation ──────────────────────────────────────────────────────────

    async def _detect(
        self, ref: SemanticRef, config: NewsConfig, day: date, user: dict, budget: CycleBudget
    ) -> tuple[list[ProofGroup], dict[str, GroupRows], list[Candidate], list[str]]:
        groups, parsed, candidates, warnings = [], {}, [], []
        for group in proof_groups(config, day):
            result, _ = await self.service.query(
                ref, SemanticPlan.from_dict(group.plan), user, budget
            )
            rows = read_group(group, config, result)
            name = f"{group.metric} by {group.dimension or 'day'}"
            if rows is None:
                warnings.append(f"{name}: the result is too large for one edition query")
                continue
            if group.dimension and len(rows.by_value) > config.max_slice_values:
                warnings.append(f"{name}: more values than the configured slice limit")
                continue
            if not rows.rows:
                warnings.append(f"{name}: the execution account reads no rows")
            groups.append(group)
            parsed[group.key] = rows
            for value, series in rows.series.items():
                found = detect_slice(series, config, day)
                if found and found[0]["detected"]:
                    candidates.append(Candidate(group, value, found[0], rows, found[1]))
        return groups, parsed, candidates, warnings

    def _story(
        self,
        candidate: Candidate,
        rank: int,
        edition: Edition,
        config: NewsConfig,
        ir: SemanticModelIR,
        parsed: dict[str, GroupRows],
    ) -> Story:
        group, detection, day = candidate.group, candidate.detection, edition.edition_date
        sliced = (
            StorySlice(dimension=group.dimension, value=candidate.value)
            if candidate.value is not None
            else None
        )
        digest, count = candidate.rows.digest(candidate.value)
        proofs = [
            StoryProof(
                group_key=group.key,
                scope="slice" if sliced else "total",
                value=candidate.value,
                digest=digest,
                rows=count,
            )
        ]
        drivers: list[Driver] = []
        if sliced is None and config.slice_dimensions:
            driver_group = group_key(group.metric, config.slice_dimensions[0])
            rows = parsed.get(driver_group)
            if rows and rows.rows:
                for value, series in rows.series.items():
                    before = median(
                        [series.get(item, (0.0, 0))[0] for item in candidate.baseline_dates]
                    )
                    after = series.get(day, (0.0, 0))[0]
                    drivers.append(
                        Driver(
                            dimension=config.slice_dimensions[0],
                            value=value,
                            before=before,
                            after=after,
                            change=after - before,
                        )
                    )
                drivers = sorted(drivers, key=lambda item: -abs(item.change))[:8]
                whole, total = rows.digest(whole=True)
                proofs.append(
                    StoryProof(group_key=driver_group, scope="group", digest=whole, rows=total)
                )
        dedup = fingerprint(
            [
                edition.semantic.view_id,
                group.metric,
                sliced.model_dump() if sliced else None,
                day.isoformat(),
                "increase" if detection["change"] > 0 else "decrease",
            ]
        )
        metric = ir.metric(group.metric)
        story = Story(
            id=fingerprint([dedup, edition.nonce]),
            scope=edition.scope,
            edition_id=edition.id,
            semantic=edition.semantic,
            metric=group.metric,
            metric_label=label(group.metric),
            unit=(metric.unit if metric else None) or None,
            slice=sliced,
            edition_date=day,
            window=edition.window,
            baseline_dates=candidate.baseline_dates,
            before=detection["before"],
            after=detection["after"],
            change=detection["change"],
            relative_change=detection["relative_change"],
            severity=detection["severity"],
            rank=rank,
            confidence=Confidence(dimension="detection", method=DETECTION_METHOD, label="medium"),
            proofs=proofs,
            series=[
                SeriesPoint(date=item, value=value, count=count)
                for item, (value, count) in sorted(candidate.rows.series[candidate.value].items())
            ],
            drivers=drivers,
            narrative=Narrative(
                headline="-", deck="-", what_happened="-", why_it_matters="-", what_to_check="-"
            ),
            dedup_key=dedup,
        )
        return story.model_copy(
            update={
                "narrative": template_narrative(story, config),
                "business_rules": business_rules(ir, story, config),
            }
        )

    async def run_cycle(self, view_id: str, user: dict, *, now: datetime | None = None) -> dict:
        view = await self.semantic._get(view_id)
        config = self._stored_config(view) if view else None
        if (
            not view
            or not view.get("news_enabled")
            or config is None
            or (config.execution_user, config.execution_role)
            != (user.get("username"), user.get("active_role"))
        ):
            return {"status": "disabled"}
        budget = CycleBudget()
        try:
            ref, ir = await self._ir(view)
            validate_config(ir, config)
        except HTTPException as exc:
            await self._audit_cycle(view, user, "FAILED", str(exc.detail))
            return {"status": "invalid_configuration"}
        try:
            await self.service.authorize_semantic(ref, user, active=True, budget=budget)
        except HTTPException as exc:
            await self._audit_cycle(view, user, "FAILED", str(exc.detail))
            raise
        day = edition_day(config, now)
        zone = ZoneInfo(config.timezone)
        window = Window(
            start=datetime.combine(day, time.min, zone),
            end=datetime.combine(day + timedelta(days=1), time.min, zone),
        )
        groups, parsed, candidates, warnings = await self._detect(ref, config, day, user, budget)
        scope = Scope.from_user(user).model_copy(update={"session_id": None})
        edition_id = fingerprint([view_id, day.isoformat(), scope.principal, scope.active_role])
        async with metadata_lock(f"newsroom:{view_id}"):
            prior = await self.repository.get("editions", edition_id, scope, Edition)
            edition = Edition(
                id=edition_id,
                scope=scope,
                semantic=ref,
                edition_date=day,
                window=window,
                groups=groups,
                nonce=prior.nonce if prior else uuid4().hex,
                pressed_at=(now or utc_now()).astimezone(UTC),
                config_digest=fingerprint(config.model_dump(mode="json")),
                warnings=warnings[:12],
            )
            stories = []
            for rank, candidate in enumerate(
                rank_candidates(candidates, config.max_stories), start=1
            ):
                story = self._story(candidate, rank, edition, config, ir, parsed)
                old = await self.repository.get("stories", story.id, scope, Story)
                if old and [item.digest for item in old.proofs] == [
                    item.digest for item in story.proofs
                ]:
                    story = story.model_copy(
                        update={
                            "narrative": old.narrative,
                            "narrative_source": old.narrative_source,
                            "narrative_model": old.narrative_model,
                            "created_at": old.created_at,
                        }
                    )
                stories.append(
                    await self.repository.save(
                        "stories", story, expected_revision=old.revision if old else 0
                    )
                )
            edition = await self.repository.save(
                "editions",
                edition.model_copy(update={"story_ids": [story.id for story in stories]}),
                expected_revision=prior.revision if prior else 0,
            )
        known_values = {
            value for rows in parsed.values() for value in rows.by_value if value is not None
        } | {
            value
            for dataset in ir.datasets
            for item in dataset.fields
            for value in item.sample_values
        }
        written, rejected = await self._write(stories, config, ir, known_values, budget)
        await self._audit_cycle(
            view,
            user,
            "SUCCESS",
            None,
            # AUDIT_LOG.decision holds 32 characters.
            decision=f"stories={len(stories)} written={written} rejected={rejected}",
        )
        return {
            "status": "pressed",
            "edition_id": edition.id,
            "edition_date": day.isoformat(),
            "stories": len(stories),
            "queries": budget.queries,
            "written": written,
            "rejected": rejected,
            "warnings": warnings,
        }

    async def _write(
        self,
        stories: list[Story],
        config: NewsConfig,
        ir: SemanticModelIR,
        known_values: set[str],
        budget: CycleBudget,
    ) -> tuple[int, int]:
        """Let the model rewrite a few template stories inside the cycle's time.

        Returns ``(accepted, rejected)``. A rejected or failed draft leaves the
        deterministic narrative in place.
        """
        written = rejected = 0
        if self.writer is None or config.narrative != "model":
            return written, rejected
        for story in stories:
            if written + rejected >= _MAX_WRITES or 120 - (monotonic() - budget.started) < 45:
                break
            if story.narrative_source != "template":
                continue
            try:
                narrative, model = await self.writer(story, config, ir, known_values)
                await self.repository.save(
                    "stories",
                    story.model_copy(
                        update={
                            "narrative": narrative,
                            "narrative_source": "model",
                            "narrative_model": model,
                        }
                    ),
                    expected_revision=story.revision,
                )
                written += 1
            except Exception:
                rejected += 1
        return written, rejected

    @staticmethod
    async def _audit_cycle(view, user, status, error, *, decision=None) -> None:
        await write_audit_log(
            event_type="INTELLIGENCE",
            user_name=user["username"],
            action="PRESS_NEWS",
            object_type="SEMANTIC_VIEW",
            object_name=view["name"],
            status=status,
            error_message=error,
            session_id=user.get("session_id"),
            active_role=user.get("active_role"),
            security_context_version=user.get("security_context_version"),
            decision=decision,
        )

    # ── Reading ─────────────────────────────────────────────────────────────

    async def _reader_digests(
        self, edition: Edition, config: NewsConfig, user: dict, budget: CycleBudget
    ) -> dict[tuple[str, str, str | None], tuple[str, int]]:
        """Run every proof query of the edition as the reader."""
        gate = asyncio.Semaphore(_READER_CONCURRENCY)

        async def run(group: ProofGroup) -> GroupRows | None:
            async with gate:
                try:
                    result, _ = await self.service.query(
                        edition.semantic, SemanticPlan.from_dict(group.plan), user, budget
                    )
                except (HTTPException, SemanticPlanError, TimeoutError):
                    return None
            return read_group(group, config, result)

        digests: dict[tuple[str, str, str | None], tuple[str, int]] = {}
        results = await asyncio.gather(*(run(group) for group in edition.groups))
        for group, rows in zip(edition.groups, results, strict=True):
            if rows is None:
                continue
            if group.dimension is None:
                digests[(group.key, "total", None)] = rows.digest(None)
            else:
                digests[(group.key, "group", None)] = rows.digest(whole=True)
                for value in rows.by_value:
                    digests[(group.key, "slice", value)] = rows.digest(value)
        return digests

    @staticmethod
    def _proven(story: Story, digests: dict) -> bool:
        return all(
            proof.rows >= 1
            and digests.get((proof.group_key, proof.scope, proof.value))
            == (proof.digest, proof.rows)
            for proof in story.proofs
        )

    async def _edition(self, view: dict, config: NewsConfig, day: date | None) -> Edition | None:
        scope = Scope(
            principal=config.execution_user,
            active_role=config.execution_role,
            security_context_version=1,
        )
        if day is None:
            return await self.repository.latest_edition(view["id"], scope, Edition)
        return await self.repository.get(
            "editions",
            fingerprint([view["id"], day.isoformat(), scope.principal, scope.active_role]),
            scope,
            Edition,
        )

    async def _visible(
        self, view: dict, user: dict, day: date | None, *, only: str | None = None
    ) -> tuple[Edition, list[Story]] | None:
        """The stories of one view that this reader proves, or ``None``."""
        config = self._stored_config(view)
        if not view.get("news_enabled") or config is None or not config.execution_user:
            return None
        edition = await self._edition(view, config, day)
        if edition is None or (only is not None and only not in edition.story_ids):
            return None
        budget = CycleBudget()
        try:
            await self.service.authorize_semantic(edition.semantic, user, budget=budget)
        except HTTPException:
            return None
        if only is not None:
            # The detail route needs only the groups this story's proofs name.
            target = await self.repository.get("stories", only, edition.scope, Story)
            if target is None:
                return None
            needed = {proof.group_key for proof in target.proofs}
            checked = edition.model_copy(
                update={"groups": [group for group in edition.groups if group.key in needed]}
            )
            stories = [target]
        else:
            checked = edition
            stories = [
                story
                for story_id in edition.story_ids
                if (story := await self.repository.get("stories", story_id, edition.scope, Story))
            ]
        digests = await self._reader_digests(checked, config, user, budget)
        return edition, sorted(
            (story for story in stories if self._proven(story, digests)),
            key=lambda story: story.rank,
        )

    async def newspaper(self, user: dict, *, day: date | None = None) -> dict:
        sections = []
        for view in (await self.semantic.news_views())[:_MAX_VIEWS]:
            found = await self._visible(view, user, day)
            if found is None:
                continue
            edition, stories = found
            sections.append(
                {
                    "view_id": view["id"],
                    "name": view["name"],
                    "edition_date": edition.edition_date.isoformat(),
                    "pressed_at": edition.pressed_at.isoformat(),
                    "stories": [public_story(story) for story in stories],
                }
            )
        shown = sum(len(section["stories"]) for section in sections)
        await self._audit_read(user, "READ_NEWSPAPER", "newspaper", "SUCCESS", "ALLOW", shown)
        return {
            "edition_date": max(
                (section["edition_date"] for section in sections), default=None
            ),
            "sections": sections,
        }

    async def story(self, story_id: str, user: dict) -> dict:
        found = None
        record = await self.repository.shared_story(story_id, Story)
        if record is not None:
            view = await self.semantic._get(record.semantic.view_id)
            if view and view["status"] != "DEPRECATED":
                found = await self._visible(view, user, record.edition_date, only=record.id)
        if not found or not found[1]:
            await self._audit_read(user, "READ_STORY", story_id[:64], "DENIED", "DENY", 0)
            raise HTTPException(status_code=404, detail=_UNAVAILABLE)
        await self._audit_read(user, "READ_STORY", story_id[:64], "SUCCESS", "ALLOW", 1)
        return public_story(found[1][0]) | {
            "view_name": view["name"],
            "pressed_at": found[0].pressed_at.isoformat(),
        }

    @staticmethod
    async def _audit_read(user, action, name, status, decision, rows) -> None:
        await write_audit_log(
            event_type="INTELLIGENCE",
            user_name=user["username"],
            action=action,
            object_type="NewsStory",
            object_name=name,
            status=status,
            rows_affected=rows,
            session_id=user.get("session_id"),
            active_role=user.get("active_role"),
            security_context_version=user.get("security_context_version"),
            decision=decision,
        )


async def _model_writer(story, config, ir, known_values):
    from app.modules.intelligence.newsroom_writer import write_story

    return await write_story(story, config, ir, known_values)


newsroom_service = NewsroomService(writer=_model_writer)
