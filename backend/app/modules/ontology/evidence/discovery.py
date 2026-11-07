"""A join-discovery run: profile every table, sketch key-like columns, propose joins, score.

The run executes as the person who starts it, through ``query_service`` with
their session and active role, so it sees and profiles exactly what they may
query. Its profiles and candidates carry that security context and are shown
only to the same principal in the same role. Sketches are never stored.

Gold joins are the relationships declared in the caller's published Semantic
Views, plus any the request supplies for tables no view covers. A proposed
join outside the gold can still be right; reviewers confirm or reject it, and
the summary reports precision against the declared joins and after review.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from pydantic import Field

from app.modules.intelligence.contracts import Contract
from app.modules.ontology import access
from app.modules.ontology.contracts import Actor
from app.modules.ontology.evaluation.contracts import EvalResult, EvalRun, result_id
from app.modules.ontology.evidence.joins import (
    JoinCandidate,
    candidates,
    score,
    select,
    verify,
    worth_verifying,
)
from app.modules.ontology.evidence.profiling import (
    SKETCH_SIZE,
    ColumnProfile,
    Sketch,
    columns_sql,
    inclusion_sql,
    parse_table_profile,
    sketch_sql,
    table_profile_sql,
    uniqueness_sql,
)

if TYPE_CHECKING:
    from app.modules.ontology.evaluation.service import EvaluationService

TABLES_PER_STEP = 6
SKETCH_PARALLEL = 4


class JoinRunRequest(Contract):
    set_name: str = Field(default="joins", min_length=1, max_length=64)
    #: Databases to profile; empty means those behind the caller's published views.
    databases: list[str] = Field(default_factory=list, max_length=20)
    #: Extra known joins, ``db.table.column`` pairs, for tables no view declares.
    gold: list[tuple[str, str]] = Field(default_factory=list, max_length=500)


def _table_key(source: str, database: str | None) -> str | None:
    parts = [part.strip("`") for part in source.split(".") if part]
    if len(parts) >= 2:
        return f"{parts[-2]}.{parts[-1]}"
    if parts and database:
        return f"{database}.{parts[0]}"
    return None


def declared_joins(models: dict[str, dict]) -> set[tuple[str, str]]:
    """The many-to-one joins the published views declare, as ``db.table.column`` pairs."""
    from app.modules.agents.semantic.ir import SemanticModelIR

    found: set[tuple[str, str]] = set()
    for model in models.values():
        ir = SemanticModelIR.from_ossie(model["definition"])
        tables = {item.name: _table_key(item.source, model.get("database_name"))
                  for item in ir.datasets}
        for rel in ir.relationships:
            if len(rel.from_columns) != 1 or len(rel.to_columns) != 1:
                continue
            left, right = tables.get(rel.from_dataset), tables.get(rel.to_dataset)
            if not left or not right:
                continue
            pair = (f"{left}.{rel.from_columns[0]}", f"{right}.{rel.to_columns[0]}")
            cardinality = rel.cardinality.replace("-", "_").lower()
            if cardinality in {"one_to_many", "1_to_many", "1:n"}:
                pair = (pair[1], pair[0])
            found.add(pair)
    return found


def same_context(run: EvalRun, user: dict) -> bool:
    """Profiles reflect what one principal saw in one role, so only that context reads them."""
    actor = Actor.from_user(user)
    return (run.actor.principal, run.actor.active_role) == (actor.principal, actor.active_role)


async def start(service: EvaluationService, body: JoinRunRequest, user: dict) -> EvalRun:
    from app.modules.ontology.evaluation.service import _new_id

    await access.require_admin("EVALUATE", "ONTOLOGY_EVALUATION", body.set_name, user)
    models = await service._models(user)
    databases = sorted(set(body.databases) or {
        str(model.get("database_name")) for model in models.values()
        if model.get("database_name")
    })
    if not databases:
        raise HTTPException(status_code=422, detail="No database to profile")
    columns: dict[str, list[list[str]]] = {}
    for database in databases:
        names, rows, error = await service._execute(
            columns_sql(database), {"database_name": database}, user)
        if error:
            raise HTTPException(status_code=404, detail=access.NOT_FOUND)
        index = {str(name).lower(): position for position, name in enumerate(names)}
        for row in rows:
            table = f"{database}.{row[index['table_name']]}"
            columns.setdefault(table, []).append(
                [str(row[index["column_name"]]), str(row[index["data_type"]])])
    if not columns:
        raise HTTPException(status_code=404, detail=access.NOT_FOUND)
    gold = sorted(declared_joins(models) | {tuple(pair) for pair in body.gold})
    run = EvalRun(
        id=_new_id("evn_", "joins"), actor=Actor.from_user(user), kind="joins",
        set_name=body.set_name, version=1, stage="profile", items=sorted(columns),
        summary={"databases": databases, "columns": columns, "gold": gold,
                 "declared": len(declared_joins(models)), "started": time.time()},
    )
    await access.audit("EVALUATE", "ONTOLOGY_EVALUATION", run.id, user)
    return await service.repository.save("eval_runs", run)


async def step(service: EvaluationService, run: EvalRun, user: dict) -> EvalRun:
    if not same_context(run, user):
        raise HTTPException(status_code=404, detail=access.NOT_FOUND)
    if run.stage == "profile":
        return await _profile(service, run, user)
    return await _discover(service, run, user)


async def _profile(service: EvaluationService, run: EvalRun, user: dict) -> EvalRun:
    batch = run.items[run.done:run.done + TABLES_PER_STEP]
    if not batch:
        elapsed = time.time() - float(run.summary.get("started") or time.time())
        return await service._save_run(run, stage="discover", summary={
            **run.summary, "profile_seconds": round(elapsed, 1)})
    results = []
    for table in batch:
        database, name = table.split(".", 1)
        columns = [(column, datatype) for column, datatype in run.summary["columns"][table]]
        names, rows, error = await service._execute(
            table_profile_sql(database, name, [column for column, _ in columns]),
            {"database_name": database}, user)
        profiles = (parse_table_profile(database, name, columns, rows[0])
                    if rows and not error else [])
        results.append(EvalResult(
            id=result_id(run.id, "profile:" + table), actor=run.actor, run_id=run.id,
            item="profile:" + table, outcome="profiled" if profiles else "failed",
            detail={"columns": [item.as_dict() for item in profiles],
                    **({"error": error} if error else {})},
        ))
    await service.repository.save_many("eval_results", [(item, 0) for item in results])
    return await service._save_run(run, done=run.done + len(batch))


async def _sketches(
    service: EvaluationService, profiles: list[ColumnProfile], user: dict
) -> dict[str, Sketch]:
    semaphore = asyncio.Semaphore(SKETCH_PARALLEL)
    sketches: dict[str, Sketch] = {}

    async def one(profile: ColumnProfile) -> None:
        async with semaphore:
            _names, rows, error = await service._execute(
                sketch_sql(profile), {"database_name": profile.database}, user)
        if not error:
            sketches[profile.key] = Sketch(
                hashes=frozenset(int(row[0]) for row in rows if row and row[0] is not None),
                ndv=profile.ndv, size=SKETCH_SIZE)

    await asyncio.gather(*(one(item) for item in profiles))
    return sketches


async def _verified(
    service: EvaluationService, found: list[JoinCandidate], profiles: list[ColumnProfile],
    user: dict,
) -> list[JoinCandidate]:
    """Exact inclusion and key uniqueness for the pairs the rule could accept."""
    by_key = {item.key: item for item in profiles}
    semaphore = asyncio.Semaphore(SKETCH_PARALLEL)

    async def run(sql: str, database: str) -> list[Any] | None:
        async with semaphore:
            _names, rows, error = await service._execute(sql, {"database_name": database}, user)
        return None if error or not rows else rows[0]

    targets = sorted({item.target for item in found if worth_verifying(item)})
    counts = await asyncio.gather(*(
        run(uniqueness_sql(by_key[key]), by_key[key].database) for key in targets))
    uniqueness = {key: row for key, row in zip(targets, counts, strict=True) if row}
    pending = [item for item in found if worth_verifying(item) and item.target in uniqueness]
    inclusion = await asyncio.gather(*(
        run(inclusion_sql(by_key[item.source], by_key[item.target]),
            by_key[item.source].database)
        for item in pending))
    verified = {}
    for item, row in zip(pending, inclusion, strict=True):
        if row is None:
            continue
        non_null, distinct = uniqueness[item.target]
        verified[(item.source, item.target)] = verify(
            item, matched=int(row[1] or 0), distinct=int(row[0] or 0),
            target_non_null=int(non_null or 0), target_distinct=int(distinct or 0))
    return [verified.get((item.source, item.target), item) for item in found]


async def _discover(service: EvaluationService, run: EvalRun, user: dict) -> EvalRun:
    started = time.perf_counter()
    stored = [item for item in await service.results(run.id) if item.item.startswith("profile:")]
    profiles = [ColumnProfile(**column) for item in stored
                for column in item.detail.get("columns") or []]
    keyish = [item for item in profiles if item.family and item.ndv >= 1]
    sketches = await _sketches(service, keyish, user)
    found = await _verified(service, candidates(profiles, sketches), profiles, user)
    chosen = {(item.source, item.target) for item in select(found)}
    physical = {item.key for item in profiles}
    declared = {tuple(pair) for pair in run.summary.get("gold") or []}
    # A view may join on a field it derives; only joins between stored columns are findable.
    gold = {pair for pair in declared if pair[0] in physical and pair[1] in physical}
    results = [
        EvalResult(
            id=result_id(run.id, f"join:{item.source}->{item.target}"), actor=run.actor,
            run_id=run.id, item=f"join:{item.source}->{item.target}",
            outcome="proposed" if (item.source, item.target) in chosen else "rejected",
            detail={**item.as_dict(),
                    "declared": (item.source, item.target) in gold
                    or (item.target, item.source) in gold,
                    "declared_direction": (item.source, item.target) in gold},
        )
        for item in found
    ]
    results, assessed = assess(results, gold)
    if results:
        await service.repository.save_many("eval_results", [(item, 0) for item in results])
    return await service._save_run(run, status="COMPLETED", stage="done", summary={
        **assessed,
        **run.summary,
        "columns": None,
        "gold": sorted(gold),
        "not_physical": sorted(declared - gold),
        "profiled_columns": len(profiles),
        "sketched_columns": len(sketches),
        "candidates": len(found),
        "verified": sum(1 for item in found if item.verified),
        "discover_seconds": round(time.perf_counter() - started, 1),
    })


def assess(
    results: list[EvalResult], gold: set[tuple[str, str]]
) -> tuple[list[EvalResult], dict[str, Any]]:
    """Scores, calibrated probabilities and automatic decisions for a run's candidates."""
    from app.modules.ontology.evaluation.service import consensus
    from app.modules.ontology.evidence.calibration import calibrate

    joins = [item for item in results if item.item.startswith("join:")]
    details = [{**item.detail, "proposed": item.outcome == "proposed"} for item in joins]
    verdicts = [consensus(item) if item.reviews else None for item in joins]
    calibrated, summary = calibrate(details, verdicts)
    updated = {
        item.id: item.model_copy(update={"detail": {
            key: value for key, value in detail.items() if key != "proposed"}})
        for item, detail in zip(joins, calibrated, strict=True)
    }
    merged = [updated.get(item.id, item) for item in results]
    return merged, {**summarise_joins(merged, gold), **summary}


def summarise_joins(results: list[EvalResult], gold: set[tuple[str, str]]) -> dict[str, Any]:
    """Scores against declared joins, and after reviewers confirmed undeclared proposals."""
    from app.modules.ontology.evaluation.service import consensus

    proposed = [item for item in results if item.outcome == "proposed"]
    pairs = [(str(item.detail["source"]), str(item.detail["target"])) for item in proposed]
    declared = score(pairs, gold)
    confirmed = set(gold)
    reviewed = 0
    for item in proposed:
        if item.detail.get("declared") or not item.reviews:
            continue
        reviewed += 1
        if consensus(item) == "yes":
            confirmed.add((str(item.detail["source"]), str(item.detail["target"])))
    return {
        "joins": declared,
        "joins_reviewed": score(pairs, confirmed) if reviewed else None,
        "undeclared_reviewed": reviewed,
    }
