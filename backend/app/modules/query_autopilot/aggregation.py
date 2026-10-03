from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from app.modules.query_autopilot.detection import (
    Facts,
    detect,
    diagnose,
    priority,
    serialize_findings,
)
from app.modules.query_autopilot.guidance import next_steps
from app.modules.query_autopilot.models import Observation, Policy, digest, utcnow
from app.modules.query_autopilot.statistics import Distribution, Window, compare_baseline


def window_start(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(minute=(value.minute // 30) * 30, second=0, microsecond=0)


async def aggregate(repo, *, now: datetime | None = None) -> dict:
    now = now or utcnow()
    policy = Policy.model_validate(await repo.get("policies", "default") or {})
    checkpoint = await repo.get("jobs", "aggregation_cursor")
    since = (
        datetime.fromisoformat(checkpoint["through"]) - timedelta(minutes=2)
        if checkpoint
        else now - timedelta(days=policy.observations_days)
    )
    touched: set[tuple[str, str, datetime]] = set()
    last = ""
    while True:
        page = await repo.page("observations", since=since, after=last, limit=1000)
        if not page:
            break
        for value in page:
            item = Observation.model_validate(value)
            touched.add((item.family_id, item.scope.cohort_id, window_start(item.observed_at)))
        last = page[-1]["id"]
    open_cursor = ""
    while True:
        unfinished = await repo.page("rollups", state="OPEN", after=open_cursor, limit=1000)
        if not unfinished:
            break
        for window in unfinished:
            touched.add(
                (window["family_id"], window["cohort_id"], datetime.fromisoformat(window["start"]))
            )
        open_cursor = unfinished[-1]["id"]
    # Recompute a touched window from all retained observations: batch retries,
    # late arrivals and a crash before checkpointing cannot double-count it.
    families = {}
    for family, cohort, start in sorted(touched):
        distribution, count, errors, last = Distribution(), 0, 0, ""
        sample = None
        while True:
            page = await repo.page(
                "observations",
                family_id=family,
                cohort_id=cohort,
                after=last,
                limit=1000,
                created_since=start,
                before=start + timedelta(minutes=30),
            )
            if not page:
                break
            for value in page:
                item = Observation.model_validate(value)
                if window_start(item.observed_at) != start:
                    continue
                count += 1
                if item.status == "success":
                    distribution.add(item.total_ms)
                else:
                    errors += 1
                if sample is None or item.observed_at > sample.observed_at:
                    sample = item
            last = page[-1]["id"]
        if sample is None:
            continue
        identifier = digest([family, cohort, start.isoformat()])
        window = {
            "id": identifier,
            "family_id": family,
            "cohort_id": cohort,
            "start": start.isoformat(),
            "complete": start + timedelta(minutes=30) <= now,
            "distribution": distribution.as_dict(),
            "count": count,
            "errors": errors,
        }
        await repo.put(
            "rollups",
            identifier,
            window,
            family_id=family,
            cohort_id=cohort,
            state="COMPLETE" if window["complete"] else "OPEN",
            created_at=start,
        )
        family_key = digest([family, cohort])
        family_record = {
            "id": family_key,
            "family_id": family,
            "cohort_id": cohort,
            "scope": sample.scope.model_dump(),
            "canonical": sample.canonical,
            "classified": sample.classified,
            "tables": sample.tables,
            "last_observed_at": sample.observed_at.isoformat(),
        }
        existing = await repo.get("families", family_key)
        if existing and existing.get("last_observed_at", "") > family_record["last_observed_at"]:
            family_record = existing
        await repo.put("families", family_key, family_record, family_id=family, cohort_id=cohort)
        families[(family, cohort)] = family_record
    # Profile delivery can lag the observation that requested it. Re-evaluate
    # the exact cohort after enrichment even when no new workload has arrived.
    evidence_cursor = ""
    while True:
        page = await repo.page("evidence", since=since, after=evidence_cursor, limit=1000)
        if not page:
            break
        for evidence in page:
            key = (evidence["family_id"], evidence["cohort_id"])
            if key not in families:
                record = await repo.get("families", digest(list(key)))
                if record and datetime.fromisoformat(record["last_observed_at"]) >= (
                    now - timedelta(minutes=30)
                ):
                    families[key] = record
        evidence_cursor = page[-1]["id"]
    family_cursor = ""
    while True:
        page = await repo.page("families", after=family_cursor, limit=1000)
        if not page:
            break
        for record in page:
            last_observed = datetime.fromisoformat(record["last_observed_at"])
            if last_observed >= now - timedelta(minutes=60) or record.get(
                "baseline", {}
            ).get("current_count", 0):
                families.setdefault((record["family_id"], record["cohort_id"]), record)
        family_cursor = page[-1]["id"]
    for (family, cohort), record in families.items():
        windows: list[Window] = []
        last = ""
        while True:
            page = await repo.page(
                "rollups", family_id=family, cohort_id=cohort, after=last, limit=1000
            )
            if not page:
                break
            windows.extend(
                Window(
                    datetime.fromisoformat(w["start"]),
                    Distribution.from_dict(w["distribution"]),
                    w["complete"],
                )
                for w in page
            )
            last = page[-1]["id"]
        windows.sort(key=lambda w: w.start)
        current_start = now - timedelta(minutes=30)
        current_distribution, previous_distribution = Distribution(), Distribution()
        cursor = ""
        while True:
            observations = await repo.page(
                "observations",
                family_id=family,
                cohort_id=cohort,
                after=cursor,
                limit=1000,
                created_since=now - timedelta(minutes=60),
                before=now,
            )
            if not observations:
                break
            for raw in observations:
                item = Observation.model_validate(raw)
                if item.status != "success" or item.observed_at < now - timedelta(minutes=60):
                    continue
                target = (
                    current_distribution
                    if item.observed_at >= current_start
                    else previous_distribution
                )
                target.add(item.total_ms)
            cursor = observations[-1]["id"]
        current = Window(current_start, current_distribution, complete=False)
        previous = Window(current_start - timedelta(minutes=30), previous_distribution)
        comparison = compare_baseline(current, windows)
        previous_comparison = compare_baseline(previous, windows)
        sustained = bool(
            previous_comparison
            and previous_comparison.eligible
            and (previous_comparison.current_p95 or 0)
            >= (previous_comparison.historical_p95 or 0) * policy.regression_ratio
            and (previous_comparison.current_p95 or 0) - (previous_comparison.historical_p95 or 0)
            >= policy.regression_absolute_ms
            and (previous_comparison.current_p95 or 0) > (previous_comparison.upper_envelope or 0)
        )
        from app.modules.query_autopilot.enrichment import measured_facts

        extras, evidence_ids = await measured_facts(
            repo, family, cohort, current.start, current.start + timedelta(minutes=30)
        )
        facts = Facts(
            current.distribution,
            comparison,
            evidence_ids=evidence_ids,
            previous_regression=sustained,
            **extras,
        )
        findings = detect(facts, policy)
        diagnoses = diagnose(findings, facts)
        outcomes = await repo.page("outcomes", family_id=family, cohort_id=cohort, limit=1000)
        gains = [
            o["measured_gain"]
            for o in outcomes
            if isinstance(o.get("measured_gain"), (int, float))
            and o.get("state") in {"SUCCESS", "NO_IMPROVEMENT", "REGRESSED"}
        ]
        score = priority(facts, diagnoses[0].confidence, outcomes=gains)
        summary = {
            "id": record["id"],
            "family_id": family,
            "cohort_id": cohort,
            "window": current.start.isoformat(),
            "baseline": asdict(comparison),
            "diagnosis": [asdict(d) for d in diagnoses],
            "next_steps": next_steps({finding.detector for finding in findings}),
            "priority": asdict(score),
            "findings": serialize_findings(findings),
        }
        await repo.put(
            "baselines",
            digest([record["id"], window_start(current.start).isoformat()]),
            summary,
            family_id=family,
            cohort_id=cohort,
            created_at=window_start(current.start),
        )
        await repo.put(
            "families", record["id"], {**record, **summary}, family_id=family, cohort_id=cohort
        )
        for finding in findings:
            identifier = digest(
                [record["id"], window_start(current.start).isoformat(), finding.detector]
            )
            await repo.put(
                "incidents",
                identifier,
                {**summary, "id": identifier, "detector": finding.detector, "state": "OPEN"},
                family_id=family,
                cohort_id=cohort,
                state="OPEN",
                insert_only=True,
            )
    await repo.put(
        "jobs",
        "aggregation_cursor",
        {"id": "aggregation_cursor", "through": now.isoformat(), "state": "CHECKPOINT"},
        state="CHECKPOINT",
    )
    return {"state": "COMPLETED", "windows": len(touched), "families": len(families)}
