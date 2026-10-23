"""Consented report schedules and deterministic in-Nova threshold delivery."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Literal

from fastapi import HTTPException
from pydantic import Field, model_validator

from app.modules.agents.automations import next_run
from app.modules.intelligence.engine_repository import metadata_lock
from app.modules.intelligence.schedules import configure_schedule, require_execution_binding
from app.modules.smart_reports.exports import collect, evidence, identity, store
from app.modules.smart_reports.schemas import Contract, Identifier, ReportSpecV1, ReportViewStateV1
from app.modules.smart_reports.service import report_service, stable_id


class Threshold(Contract):
    widget_id: Identifier
    metric: str = Field(min_length=1, max_length=128)
    comparator: Literal[">", ">=", "<", "<=", "=", "!="]
    value: Decimal
    window: Literal["last_7_days", "last_30_days", "this_month", "this_year"]
    cooldown_minutes: int = Field(default=60, ge=15, le=525600)


class SubscriptionRequest(Contract):
    operation_id: Identifier
    subscription_id: Identifier | None = None
    expected_revision: int = Field(default=0, ge=0)
    view_session_id: Identifier
    expected_state_revision: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=256)
    schedule_kind: Literal["interval", "cron"]
    schedule_expr: str = Field(min_length=1, max_length=128)
    timezone: str = Field(min_length=1, max_length=64)
    enabled: bool = True
    consent_execution: Literal[True]
    threshold: Threshold | None = None

    @model_validator(mode="after")
    def valid_schedule(self):
        next_run(self.schedule_kind, self.schedule_expr, self.timezone, datetime.now(UTC))
        return self


def threshold_met(rule: Threshold, result: dict) -> bool:
    if (
        result["status"] != "success"
        or result["completeness"] != "complete"
        or len(result["rows"]) != 1
    ):
        raise ValueError("Alert requires one complete governed metric")
    name = rule.metric.replace(".", "__")
    index = result["columns"].index(name)
    value = result["rows"][0][index]
    if value is None:
        return False
    actual = Decimal(str(value))
    return {
        ">": actual > rule.value,
        ">=": actual >= rule.value,
        "<": actual < rule.value,
        "<=": actual <= rule.value,
        "=": actual == rule.value,
        "!=": actual != rule.value,
    }[rule.comparator]


async def configure(report_id: str, body: SubscriptionRequest, user: dict) -> dict:
    spec, state = await report_service.view(report_id, body.view_session_id, user)
    if state.draft_id or state.state_revision != body.expected_state_revision:
        raise HTTPException(409, "Subscribe from the current published report view")
    if body.enabled:
        await require_execution_binding(user)
    if body.threshold:
        widget = spec.widgets().get(body.threshold.widget_id)
        if (
            not widget
            or widget.kind != "kpi"
            or not widget.query
            or not widget.query.time
            or widget.query.time.compare
            or body.threshold.metric not in widget.query.metrics
        ):
            raise HTTPException(
                422, "Choose a KPI with a time dimension, a selected metric and no comparison"
            )
    subscription_id = body.subscription_id or stable_id(
        report_id, "subscription", user["username"], body.operation_id
    )
    async with metadata_lock(f"report-subscription:{subscription_id}") as lease:
        previous = await report_service.repo.read(subscription_id)
        if body.subscription_id and previous is None:
            raise HTTPException(404, "Subscription not found")
        if previous and (
            previous["payload"].get("kind") != "subscription"
            or previous["payload"].get("report_id") != report_id
            or previous["payload"].get("identity") != identity(user)
        ):
            raise HTTPException(404, "Subscription not found")
        record = await report_service.repo.append(
            record_id=subscription_id,
            report_id=report_id,
            kind="subscription",
            owner=user["username"],
            operation_id=body.operation_id,
            expected_revision=body.expected_revision,
            request=body.model_dump(mode="json"),
            lease=lease,
            payload={
                "kind": "subscription",
                "report_id": report_id,
                "identity": identity(user),
                "configuration": body.model_dump(mode="json"),
                "state": state.model_dump(mode="json"),
            },
        )
        # A retry after a journal write must still reconcile the scheduler record.
        schedule = await configure_schedule(
            SimpleNamespace(id=subscription_id),
            user,
            handler="reports.distribute",
            enabled=body.enabled,
            schedule_kind=body.schedule_kind,
            schedule_expr=body.schedule_expr,
            timezone=body.timezone,
        )
    await report_service.audit(report_id, user, "subscription")
    return {"subscription_id": subscription_id, "revision": record["revision"], **schedule}


async def run_subscription(
    subscription_id: str, user: dict, execution_id: str, *, instant=None
) -> dict:
    clock = instant or datetime.now(UTC)
    subscription = await report_service.repo.read(subscription_id)
    if (
        not subscription
        or subscription["payload"].get("kind") != "subscription"
        or subscription["payload"].get("identity") != identity(user)
    ):
        raise HTTPException(404, "Subscription unavailable")
    payload = subscription["payload"]
    report_id = payload["report_id"]
    body = SubscriptionRequest.model_validate(payload["configuration"])
    if not body.enabled:
        return {"status": "disabled"}
    await require_execution_binding(user)
    await report_service.access(report_id, user)
    activity_id = stable_id(subscription_id, "activity", execution_id)
    async with metadata_lock(f"report-delivery:{subscription_id}", timeout_seconds=120) as lease:
        previous = await report_service.repo.read(activity_id)
        if previous is None:
            after = ""
            while True:
                identities = await report_service.repo.ids(
                    "activity", report_id=report_id, after=after
                )
                for record_id in identities:
                    candidate = await report_service.repo.read(record_id)
                    if (
                        candidate
                        and candidate["payload"].get("subscription_id") == subscription_id
                        and candidate["payload"].get("phase") == "prepared"
                    ):
                        previous, activity_id = candidate, record_id
                        execution_id = candidate["payload"]["execution_id"]
                        break
                if previous or len(identities) < 100:
                    break
                after = identities[-1]
        if previous and previous["payload"].get("phase") == "complete":
            if body.threshold and previous["payload"]["status"] == "delivered":
                await record_cooldown(
                    subscription_id,
                    report_id,
                    execution_id,
                    previous["payload"]["evaluated_at"],
                    user,
                    lease,
                )
            return {"status": previous["payload"]["status"], "activity_id": activity_id}
        if previous and (
            previous["payload"].get("identity") != identity(user)
            or previous["payload"].get("subscription_checksum") != subscription["checksum"]
        ):
            await report_service.repo.append(
                record_id=activity_id,
                report_id=report_id,
                kind="activity",
                owner=user["username"],
                operation_id=stable_id(execution_id, "superseded"),
                expected_revision=previous["revision"],
                request={"subscription_checksum": subscription["checksum"]},
                payload={**previous["payload"], "phase": "complete", "status": "cancelled"},
                lease=lease,
            )
            raise HTTPException(409, "Subscription changed before delivery recovery")
        if previous:
            clock = datetime.fromisoformat(previous["payload"]["evaluated_at"])
        report = await report_service.access(report_id, user)
        spec, revision = await report_service.spec(
            report_id, user, revision=report["published_revision"]
        )
        if revision != payload["state"]["spec_revision"]:
            raise HTTPException(409, "Report version changed; review the subscription")
        state = ReportViewStateV1.model_validate(payload["state"]).model_copy(
            update={"spec_revision": revision, "origin": None}
        )
        if body.threshold:
            widget = spec.widgets().get(body.threshold.widget_id)
            if (
                not widget
                or widget.kind != "kpi"
                or not widget.query
                or not widget.query.time
                or widget.query.time.compare
            ):
                raise HTTPException(409, "Alert definition changed; review the subscription")
            widget.query.time.range = body.threshold.window
        snapshot = await collect(spec, state, user, as_of=clock)
        status = "delivered"
        if body.threshold:
            value = next(
                w
                for page in snapshot["pages"]
                for w in page["result"]["widgets"]
                if w["widget_id"] == body.threshold.widget_id
            )
            if not threshold_met(body.threshold, value):
                status = "condition_not_met"
            history = await report_service.repo.read(stable_id(subscription_id, "cooldown"))
            if history and clock < datetime.fromisoformat(
                history["payload"]["delivered_at"]
            ) + timedelta(minutes=body.threshold.cooldown_minutes):
                status = "cooldown"
        if previous:
            if previous["payload"].get("evidence") != evidence(snapshot):
                raise HTTPException(409, "Accessible data changed while recovering report delivery")
            status = previous["payload"]["status"]
        current = await report_service.repo.read(subscription_id)
        await report_service.access(report_id, user)
        if not current or current["checksum"] != subscription["checksum"]:
            raise HTTPException(409, "Subscription changed during execution")
        if previous:
            snapshot_uri = previous["payload"]["snapshot_uri"]
            snapshot_checksum = previous["payload"]["snapshot_checksum"]
        else:
            encoded = json.dumps(snapshot).encode()
            snapshot_checksum = hashlib.sha256(encoded).hexdigest()
            snapshot_uri = await asyncio.to_thread(
                store().put, f"smart-reports/activity/{activity_id}/snapshot.json", encoded
            )
        prepared = {
            "kind": "activity",
            "report_id": report_id,
            "subscription_id": subscription_id,
            "identity": identity(user),
            "status": status,
            "title": body.title,
            "evaluated_at": clock.isoformat(),
            "report_revision": revision,
            "thread_id": None,
            "phase": "prepared",
            "evidence": evidence(snapshot),
            "execution_id": execution_id,
            "subscription_checksum": subscription["checksum"],
            "snapshot_uri": snapshot_uri,
            "snapshot_checksum": snapshot_checksum,
        }
        if not previous:
            previous = await report_service.repo.append(
                record_id=activity_id,
                report_id=report_id,
                kind="activity",
                owner=user["username"],
                operation_id=stable_id(execution_id, "prepare"),
                expected_revision=0,
                request={"execution_id": execution_id},
                payload=prepared,
                lease=lease,
            )
        thread_id = None
        if status == "delivered":
            from app.modules.agents.identity import SMART_AGENT_ID
            from app.modules.assistant.repository import assistant_repository
            from app.modules.assistant.security import observation_context, session_security

            thread_id = stable_id(activity_id, "thread")
            await assistant_repository.create_thread(
                user_name=user["username"],
                title=body.title,
                agent_id=SMART_AGENT_ID,
                thread_id=thread_id,
            )
            await assistant_repository.append_message(
                thread_id,
                user_name=user["username"],
                message_id=stable_id(activity_id, "message"),
                role="assistant",
                agent_id=SMART_AGENT_ID,
                content=f"{body.title} is ready. "
                "Open the report to review your current authorized data.",
                security_context=observation_context(session_security(user)),
                steps=[
                    {
                        "kind": "smart_report",
                        "content_index": 0,
                        "content_id": stable_id(activity_id, "block"),
                        "report_id": report_id,
                        "published_revision": revision,
                        "title": spec.title,
                        "fallback": "Open the report to review current authorized data.",
                    }
                ],
            )
        if body.threshold and status == "delivered":
            await record_cooldown(
                subscription_id, report_id, execution_id, clock.isoformat(), user, lease
            )
        activity = {**previous["payload"], "thread_id": thread_id, "phase": "complete"}
        await report_service.repo.append(
            record_id=activity_id,
            report_id=report_id,
            kind="activity",
            owner=user["username"],
            operation_id=execution_id,
            expected_revision=previous["revision"],
            request={"execution_id": execution_id},
            payload=activity,
            lease=lease,
        )
    await report_service.audit(report_id, user, "delivery")
    return {"status": status, "activity_id": activity_id}


async def list_private(report_id: str, kind: str, user: dict) -> dict:
    await report_service.access(report_id, user)
    records = []
    after = ""
    identities = []
    while True:
        batch = await report_service.repo.ids(kind, report_id=report_id, after=after)
        identities.extend(batch)
        if len(batch) < 100:
            break
        after = batch[-1]
    for record_id in identities:
        record = await report_service.repo.read(record_id)
        if (
            record
            and record["payload"].get("identity") == identity(user)
            and (kind != "activity" or record["payload"].get("phase") == "complete")
        ):
            payload = {
                k: v
                for k, v in record["payload"].items()
                if k
                not in {
                    "identity",
                    "state",
                    "evidence",
                    "phase",
                    "snapshot_uri",
                    "snapshot_checksum",
                    "subscription_checksum",
                }
            }
            records.append({"id": record_id, "revision": record["revision"], **payload})
    records.sort(key=lambda row: row.get("evaluated_at", row["id"]), reverse=True)
    return {"items": records}


async def record_cooldown(subscription_id, report_id, execution_id, delivered_at, user, lease):
    cooldown_id = stable_id(subscription_id, "cooldown")
    history = await report_service.repo.read(cooldown_id)
    if history and history["payload"]["delivered_at"] > delivered_at:
        return
    await report_service.repo.append(
        record_id=cooldown_id,
        report_id=report_id,
        kind="cooldown",
        owner=user["username"],
        operation_id=execution_id,
        expected_revision=history["revision"] if history else 0,
        request={"execution_id": execution_id},
        payload={"delivered_at": delivered_at},
        lease=lease,
    )


async def activity_snapshot(report_id: str, activity_id: str, user: dict) -> dict:
    await report_service.access(report_id, user)
    record = await report_service.repo.read(activity_id)
    if (
        not record
        or record["payload"].get("kind") != "activity"
        or record["payload"].get("report_id") != report_id
        or record["payload"].get("identity") != identity(user)
        or record["payload"].get("phase") != "complete"
        or record["payload"].get("status") != "delivered"
    ):
        raise HTTPException(404, "Report result not found")
    payload = record["payload"]
    encoded = await asyncio.to_thread(store().get, payload["snapshot_uri"])
    if hashlib.sha256(encoded).hexdigest() != payload["snapshot_checksum"]:
        raise HTTPException(409, "Report result integrity check failed")
    snapshot = json.loads(encoded)
    spec = ReportSpecV1.model_validate(snapshot["spec"])
    state = ReportViewStateV1.model_validate(snapshot["pages"][0]["state"])
    current = await collect(
        spec, state, user, as_of=datetime.fromisoformat(payload["evaluated_at"])
    )
    if evidence(current) != payload["evidence"]:
        raise HTTPException(409, "Accessible data changed; historical results cannot be released")
    await report_service.access(report_id, user)
    await report_service.audit(report_id, user, "activity-read")
    return snapshot
