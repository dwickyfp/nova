"""Report API acceptance using the existing patched-FE/Ranger fixture."""

import os
from uuid import uuid4

import pytest

from app.core.database import db
from app.core.redis import session_store
from app.modules.intelligence.engine_repository import metadata_lock
from app.modules.smart_reports.repository import report_repository
from app.modules.smart_reports.sessions import view_sessions
from tests.integration.test_governed_studio_ranger import _user
from tests.integration.test_governed_studio_ranger import governed as governed

pytestmark = [
    pytest.mark.engine,
    pytest.mark.skipif(
        os.getenv("NOVA_GOVERNED_STUDIO_RANGER_ACCEPTANCE") != "1",
        reason="Requires the existing patched FE and configured Ranger acceptance environment",
    ),
]


async def test_report_published_acl_same_role_rls_handoff_and_revocation(governed):
    fixture = governed
    await report_repository.ensure_schema()
    source = {
        "id": "sales",
        "view_id": fixture.semantic["view_id"],
        "version": fixture.semantic["version"],
        "fingerprint": fixture.semantic["fingerprint"],
    }
    spec = {
        "title": "Governed city revenue",
        "sources": [source],
        "filters": [
            {
                "id": "city",
                "label": "City",
                "source": "sales",
                "field": "city",
                "kind": "single",
                "default": {"kind": "single", "values": ["Jakarta"]},
            }
        ],
        "pages": [
            {
                "id": "overview",
                "title": "Overview",
                "sections": [
                    {
                        "id": "main",
                        "widgets": [
                            {
                                "id": "revenue",
                                "title": "Revenue",
                                "kind": "kpi",
                                "query": {"source": "sales", "metrics": ["revenue"]},
                            },
                        ],
                    }
                ],
            }
        ],
    }
    prefix = "agents/studio/reports"
    created = await fixture.primary.request(
        "POST", prefix, {"operation_id": uuid4().hex, "spec": spec}
    )
    report_id, draft_id = created["report_id"], created["draft_id"]
    path = f"{prefix}/{report_id}"
    keys = []
    try:
        origin = {"agent_id": fixture.agent["agent_id"], "thread_id": fixture.thread["thread_id"]}
        state = await fixture.primary.request(
            "POST",
            f"{path}/view-sessions",
            {
                "draft_id": draft_id,
                "origin": origin,
            },
        )
        keys.append(view_sessions.key(fixture.user, state["session_id"]))
        rendered = await fixture.primary.request(
            "POST",
            f"{path}/render",
            {
                "view_session_id": state["session_id"],
                "expected_state_revision": 1,
            },
        )
        assert rendered["widgets"][0]["rows"] == [["1980.00"]]
        await fixture.primary.request(
            "POST",
            f"{path}/drafts/{draft_id}/publish",
            {
                "operation_id": uuid4().hex,
                "expected_revision": 1,
                "expected_published_revision": 0,
            },
        )
        await fixture.primary.request(
            "POST",
            f"{path}/grants",
            {
                "operation_id": uuid4().hex,
                "expected_revision": 0,
                "target_type": "role",
                "target_name": fixture.role,
                "permission": "viewer",
            },
        )
        peer = await _user(fixture.secondary)
        other = await fixture.secondary.request("POST", f"{path}/view-sessions", {})
        keys.append(view_sessions.key(peer, other["session_id"]))
        options = await fixture.secondary.request(
            "GET", f"{path}/filter-options/city?view_session_id={other['session_id']}"
        )
        assert options["values"] == ["Bandung"]
        changed = await fixture.secondary.request(
            "POST",
            f"{path}/view-sessions/{other['session_id']}/events",
            {
                "event_id": uuid4().hex,
                "spec_revision": 1,
                "expected_state_revision": 1,
                "type": "FILTER_SET",
                "target": "city",
                "value": {"kind": "single", "values": ["Bandung"]},
            },
        )
        result = await fixture.secondary.request(
            "POST",
            f"{path}/render",
            {
                "view_session_id": other["session_id"],
                "expected_state_revision": changed["state"]["state_revision"],
            },
        )
        assert result["widgets"][0]["rows"] == [["3960.00"]]
        handoff = await fixture.primary.request(
            "POST",
            f"{path}/handoffs",
            {
                "view_session_id": state["session_id"],
                "expected_state_revision": 1,
            },
        )
        stolen = await fixture.secondary.http.post(
            f"/api/v1/{prefix}/handoffs/consume", json={"handoff_id": handoff["handoff_id"]}
        )
        assert stolen.status_code == 410
        opened = await fixture.primary.request(
            "POST", f"{prefix}/handoffs/consume", {"handoff_id": handoff["handoff_id"]}
        )
        assert opened["session_id"] == state["session_id"]
        await fixture.primary.request(
            "POST",
            f"{path}/grants",
            {
                "operation_id": uuid4().hex,
                "expected_revision": 1,
                "target_type": "role",
                "target_name": fixture.role,
                "permission": "viewer",
                "revoked": True,
            },
        )
        revoked = await fixture.secondary.http.post(
            f"/api/v1/{path}/render",
            json={
                "view_session_id": other["session_id"],
                "expected_state_revision": 2,
            },
        )
        assert revoked.status_code == 404
        await fixture.primary.request("POST", "auth/switch-role", {"role": fixture.inactive})
        switched = await fixture.primary.http.get(
            f"/api/v1/{path}/view-sessions/{state['session_id']}"
        )
        assert switched.status_code == 410
    finally:
        if keys:
            await session_store._redis.delete(*keys)
        await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CONFIG_SMART_REPORT_EVENTS WHERE owner_name=%s",
            [fixture.principal],
        )


async def test_editor_preview_review_and_publisher_keep_published_revision_immutable(governed):
    fixture = governed
    await report_repository.ensure_schema()
    spec = {
        "title": "Published revenue",
        "sources": [{"id": "sales", **fixture.semantic}],
        "pages": [
            {
                "id": "overview",
                "title": "Overview",
                "sections": [
                    {
                        "id": "main",
                        "widgets": [
                            {
                                "id": "revenue",
                                "title": "Revenue",
                                "kind": "kpi",
                                "query": {"source": "sales", "metrics": ["revenue"]},
                            }
                        ],
                    }
                ],
            }
        ],
    }
    created = await fixture.primary.request(
        "POST", "agents/studio/reports", {"operation_id": uuid4().hex, "spec": spec}
    )
    report_id, owner_draft = created["report_id"], created["draft_id"]
    path = f"agents/studio/reports/{report_id}"
    owner_path = f"{path}/drafts/{owner_draft}"
    try:
        await fixture.primary.request(
            "POST",
            f"{owner_path}/publish",
            {
                "operation_id": uuid4().hex,
                "expected_revision": 1,
                "expected_published_revision": 0,
            },
        )
        original = await fixture.primary.request("GET", f"{path}/revisions/1")
        await fixture.primary.request(
            "POST",
            f"{path}/grants",
            {
                "operation_id": uuid4().hex,
                "expected_revision": 0,
                "target_type": "user",
                "target_name": fixture.other,
                "permission": "editor",
            },
        )
        editor = await fixture.secondary.request(
            "POST", f"{path}/drafts", {"operation_id": uuid4().hex}
        )
        editor_path = f"{path}/drafts/{editor['draft_id']}"
        patch = {
            "operation_id": uuid4().hex,
            "expected_revision": 1,
            "operations": [{"op": "SET_REPORT_METADATA", "value": {"title": "Reviewed revenue"}}],
        }
        async with metadata_lock(f"smart-report:{report_id}"):
            preview = await fixture.secondary.request(
                "POST", f"{editor_path}/preview-render", {"patch": patch}
            )
        assert preview["result"]["widgets"][0]["rows"] == [["3960.00"]]
        assert (await fixture.secondary.request("GET", editor_path))["revision"] == 1
        edited = await fixture.secondary.request("PATCH", editor_path, patch)
        assert edited["revision"] == 2
        assert await fixture.secondary.request("PATCH", editor_path, patch) == edited
        denied = await fixture.secondary.http.post(
            f"/api/v1/{editor_path}/publish",
            json={
                "operation_id": uuid4().hex,
                "expected_revision": 2,
                "expected_published_revision": 1,
            },
        )
        assert denied.status_code == 404
        submitted = await fixture.secondary.request(
            "POST",
            f"{editor_path}/reviews",
            {
                "operation_id": uuid4().hex,
                "expected_revision": 2,
            },
        )
        await fixture.secondary.request(
            "PATCH",
            editor_path,
            {
                "operation_id": uuid4().hex,
                "expected_revision": 2,
                "operations": [
                    {"op": "SET_REPORT_METADATA", "value": {"title": "Private later edit"}}
                ],
            },
        )
        private = await fixture.primary.http.get(f"/api/v1/{editor_path}")
        assert private.status_code == 404
        reviewed = await fixture.primary.request("GET", f"{path}/reviews/{submitted['review_id']}")
        assert reviewed["spec"]["title"] == "Reviewed revenue"
        assert await fixture.primary.request("GET", f"{path}/revisions/1") == original
        copied = await fixture.primary.request(
            "POST",
            f"{owner_path}/rebase",
            {
                "operation_id": uuid4().hex,
                "expected_revision": 1,
                "expected_published_revision": 1,
                "review_id": submitted["review_id"],
            },
        )
        assert copied["revision"] == 2
        assert (await fixture.primary.request("GET", path))["published_revision"] == 1
        stale = await fixture.primary.http.post(
            f"/api/v1/{owner_path}/publish",
            json={
                "operation_id": uuid4().hex,
                "expected_revision": 1,
                "expected_published_revision": 1,
            },
        )
        assert stale.status_code == 409
        release = {
            "operation_id": uuid4().hex,
            "expected_revision": 2,
            "expected_published_revision": 1,
        }
        published = await fixture.primary.request("POST", f"{owner_path}/publish", release)
        assert await fixture.primary.request("POST", f"{owner_path}/publish", release) == published
        assert (await fixture.primary.request("GET", path))["published_revision"] == 2
        assert (await fixture.primary.request("GET", f"{path}/revisions/1"))["spec"][
            "title"
        ] == "Published revenue"
        assert (await fixture.primary.request("GET", f"{path}/revisions/2"))["spec"][
            "title"
        ] == "Reviewed revenue"
    finally:
        await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CONFIG_SMART_REPORT_EVENTS WHERE report_id=%s", [report_id]
        )


async def test_report_exports_templates_and_in_nova_delivery_reauthorize(governed):
    import asyncio
    from contextlib import AsyncExitStack

    from fastapi import HTTPException

    from app.core.config import settings
    from app.modules.intelligence.contracts import Scope
    from app.modules.query.admission import query_admission
    from app.modules.smart_reports.distribution import run_subscription
    from app.modules.smart_reports.exports import store
    from app.modules.smart_reports.service import stable_id
    from app.modules.task_orchestration.credentials import StaticCredentialProvider
    from app.modules.task_orchestration.execution import DelegateExecutor
    from app.modules.task_orchestration.internal_handlers import (
        InternalTaskConfiguration,
        run_internal_task,
        run_once,
    )
    from app.modules.task_orchestration.repository import task_orchestration_repository
    from app.modules.task_orchestration.worker import GraphRunJob

    fixture = governed
    await report_repository.ensure_schema()
    spec = {
        "title": "Scheduled revenue",
        "sources": [{"id": "sales", **fixture.semantic}],
        "pages": [
            {
                "id": "overview",
                "title": "Overview",
                "sections": [
                    {
                        "id": "main",
                        "widgets": [
                            {
                                "id": "revenue",
                                "title": "Revenue",
                                "kind": "kpi",
                                "query": {
                                    "source": "sales",
                                    "metrics": ["revenue"],
                                    "time": {"dimension": "ordered_at", "range": "last_30_days"},
                                },
                            }
                        ],
                    }
                ],
            }
        ],
    }
    created = await fixture.primary.request(
        "POST", "agents/studio/reports", {"operation_id": uuid4().hex, "spec": spec}
    )
    report_id = created["report_id"]
    path = f"agents/studio/reports/{report_id}"
    cleanup_reports = [report_id]
    task = graph_run = task_run = None
    try:
        await fixture.primary.request(
            "POST",
            f"{path}/drafts/{created['draft_id']}/publish",
            {"operation_id": uuid4().hex, "expected_revision": 1, "expected_published_revision": 0},
        )
        state = await fixture.primary.request("POST", f"{path}/view-sessions", {})
        reference = {"view_session_id": state["session_id"], "expected_state_revision": 1}
        template = await fixture.primary.request(
            "POST", f"{path}/templates", {**reference, "operation_id": uuid4().hex}
        )
        copied = await fixture.primary.request(
            "POST",
            f"agents/studio/reports/templates/{template['template_id']}/instantiate",
            {"operation_id": uuid4().hex},
        )
        cleanup_reports.append(copied["report_id"])
        denied = await fixture.secondary.http.post(
            f"/api/v1/agents/studio/reports/templates/{template['template_id']}/instantiate",
            json={"operation_id": uuid4().hex},
        )
        assert denied.status_code == 404
        export_request = {**reference, "operation_id": uuid4().hex, "format": "pdf"}
        async with AsyncExitStack() as occupied:
            for _ in range(settings.REPORT_EXPORT_MAX_CONCURRENCY):
                await occupied.enter_async_context(query_admission.slot("report_exports"))
            refused = await fixture.primary.http.post(
                f"/api/v1/{path}/exports", json=export_request,
            )
            assert refused.status_code == 429 and refused.headers["Retry-After"] == "1"
            assert await report_repository.ids("export", report_id=report_id) == []
        retried = await fixture.primary.request("POST", f"{path}/exports", export_request)
        pdf = await fixture.primary.http.get(
            f"/api/v1/{path}/exports/{retried['export_id']}/download"
        )
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
        exported = await fixture.primary.request(
            "POST", f"{path}/exports", {**reference, "operation_id": uuid4().hex, "format": "xlsx"}
        )
        downloaded = await fixture.primary.http.get(
            f"/api/v1/{path}/exports/{exported['export_id']}/download"
        )
        assert downloaded.status_code == 200 and downloaded.content.startswith(b"PK")
        await task_orchestration_repository.bind_role_execution_user(
            fixture.role, fixture.principal, "nova_admin"
        )
        subscription = await fixture.primary.request(
            "POST",
            f"{path}/subscriptions",
            {
                **reference,
                "operation_id": uuid4().hex,
                "title": "Daily revenue",
                "consent_execution": True,
                "schedule_kind": "cron",
                "schedule_expr": "0 9 * * *",
                "timezone": "Asia/Jakarta",
                "threshold": {
                    "widget_id": "revenue",
                    "metric": "revenue",
                    "comparator": ">",
                    "value": "0",
                    "window": "last_30_days",
                    "cooldown_minutes": 60,
                },
            },
        )
        task = await task_orchestration_repository.get_task(subscription["task_id"])
        assert task["handler"] == "reports.distribute" and task["timezone"] == "Asia/Jakarta"
        config = InternalTaskConfiguration(
            scope=Scope.from_user(fixture.user), record_id=subscription["subscription_id"]
        )
        graph_run = await task_orchestration_repository.create_graph_run(
            {
                "graph_id": task["id"],
                "state": "running",
                "trigger_type": "schedule",
                "execution_user": fixture.principal,
                "execution_role": fixture.role,
            }
        )
        task_run = await task_orchestration_repository.create_task_run(
            {"graph_run_id": graph_run["id"], "task_id": task["id"], "state": "running"}
        )
        executor = DelegateExecutor(StaticCredentialProvider({fixture.principal: fixture.password}))
        await run_internal_task(
            task,
            GraphRunJob(
                graph_run_id=graph_run["id"],
                graph_id=task["id"],
                execution_user=fixture.principal,
                execution_role=fixture.role,
            ),
            task_run["id"],
            executor,
            task_orchestration_repository,
        )
        activity_id = stable_id(subscription["subscription_id"], "activity", graph_run["id"])
        scheduled = await report_repository.read(activity_id)
        delivery = {"status": scheduled["payload"]["status"], "activity_id": activity_id}
        assert delivery["status"] == "delivered"
        assert (
            await run_once("reports.distribute", config, fixture.user, execution_id=graph_run["id"])
            == delivery
        )
        saved = await fixture.primary.request(
            "GET", f"{path}/activity/{delivery['activity_id']}/snapshot"
        )
        assert (
            len(saved["pages"]) == 1
            and saved["pages"][0]["result"]["widgets"][0]["status"] == "success"
        )
        activity = await fixture.primary.request("GET", f"{path}/activity")
        assert len(activity["items"]) == 1 and activity["items"][0]["thread_id"]
        assert "snapshot_uri" not in activity["items"][0]
        cooldown = await run_once(
            "reports.distribute", config, fixture.user, execution_id="scheduled-two"
        )
        assert cooldown["status"] == "cooldown"
        await task_orchestration_repository.bind_role_execution_user(
            fixture.role, fixture.other, "nova_admin"
        )
        with pytest.raises(HTTPException) as revoked:
            await run_subscription(subscription["subscription_id"], fixture.user, "scheduled-three")
        assert revoked.value.status_code == 403
    finally:
        if task_run:
            await task_orchestration_repository.delete_task_run(task_run["id"])
        if graph_run:
            await task_orchestration_repository.delete_graph_run(graph_run["id"])
        if task:
            await task_orchestration_repository.delete_task(task["id"])
        for report in cleanup_reports:
            for kind in ("export", "activity"):
                for record_id in await report_repository.ids(kind, report_id=report):
                    record = await report_repository.read(record_id)
                    for field in ("uri", "snapshot_uri"):
                        if record["payload"].get(field):
                            await asyncio.to_thread(store().delete, record["payload"][field])
            await db.execute_system(
                "DELETE FROM NOVA_SYSTEM.CONFIG_SMART_REPORT_EVENTS WHERE report_id=%s", [report]
            )


async def test_row_detail_retains_ranger_scope_and_published_grain(governed):
    import json
    from copy import deepcopy

    from app.modules.agents.semantic.serialize import to_ossie_document
    from tests.integration.test_governed_studio_ranger import _publish

    fixture = governed
    definition = deepcopy(fixture.definition)
    definition["name"] = "governed_detail"
    definition["datasets"][0]["fields"][0]["dimension"] = {}
    source = await fixture.primary.request(
        "POST",
        "semantic-views",
        {
            "name": "governed_detail",
            "database": fixture.database,
            "definition": json.dumps(to_ossie_document(definition)),
        },
    )
    await _publish(fixture.primary, source["id"], 1)
    source = await fixture.primary.request("GET", f"semantic-views/{source['id']}")
    report = None
    try:
        report = await fixture.primary.request(
            "POST",
            "agents/studio/reports",
            {
                "operation_id": uuid4().hex,
                "spec": {
                    "title": "Row detail acceptance",
                    "sources": [
                        {
                            "id": "sales",
                            "view_id": source["id"],
                            "version": 1,
                            "fingerprint": source["versions"][0]["fingerprint"],
                        }
                    ],
                    "pages": [
                        {
                            "id": "overview",
                            "title": "Overview",
                            "sections": [
                                {
                                    "id": "main",
                                    "widgets": [
                                        {
                                            "id": "cities",
                                            "title": "Revenue",
                                            "kind": "bar",
                                            "query": {
                                                "source": "sales",
                                                "dimensions": ["city"],
                                                "metrics": ["revenue"],
                                            },
                                            "encoding": {"x": "city", "y": ["revenue"]},
                                            "detail_fields": ["id", "city"],
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                },
            },
        )
        path = f"agents/studio/reports/{report['report_id']}"
        state = await fixture.primary.request(
            "POST",
            f"{path}/view-sessions",
            {
                "draft_id": report["draft_id"],
            },
        )
        request = {
            "view_session_id": state["session_id"],
            "expected_state_revision": 1,
            "field": "city",
            "values": ["Jakarta"],
        }
        details = await fixture.primary.request("POST", f"{path}/widgets/cities/detail", request)
        rows = details["widgets"][0]["rows"]
        assert rows and all(row[1] == "Jakarta" for row in rows)
        assert len({row[0] for row in rows}) == len(rows)
        denied_rows = await fixture.primary.request(
            "POST",
            f"{path}/widgets/cities/detail",
            {
                **request,
                "values": ["Bandung"],
            },
        )
        assert denied_rows["widgets"][0]["rows"] == []
        invalid = await fixture.primary.http.post(
            f"/api/v1/{path}/widgets/cities/detail",
            json={
                **request,
                "field": "amount",
            },
        )
        assert invalid.status_code == 422
        stolen = await fixture.secondary.http.post(
            f"/api/v1/{path}/widgets/cities/detail",
            json=request,
        )
        assert stolen.status_code == 410
    finally:
        if report:
            await db.execute_system(
                "DELETE FROM NOVA_SYSTEM.CONFIG_SMART_REPORT_EVENTS WHERE report_id=%s",
                [report["report_id"]],
            )
        await fixture.primary.request("DELETE", f"semantic-views/{source['id']}")


async def test_eight_widget_render_and_filter_latency(governed, tmp_path):
    import json
    from time import perf_counter

    fixture = governed
    widgets = [
        {
            "id": f"metric-{i}",
            "title": f"Revenue {i + 1}",
            "placement": {"col": (i % 2) * 6, "row": (i // 2) * 3, "width": 6, "height": 3},
            "kind": "kpi",
            "query": {"source": "sales", "metrics": ["revenue"]},
        }
        for i in range(8)
    ]
    report = await fixture.primary.request(
        "POST",
        "agents/studio/reports",
        {
            "operation_id": uuid4().hex,
            "spec": {
                "title": "Eight widget latency",
                "sources": [{"id": "sales", **fixture.semantic}],
                "filters": [
                    {
                        "id": "city",
                        "label": "City",
                        "source": "sales",
                        "field": "city",
                        "kind": "single",
                        "default": {"kind": "single", "values": ["Jakarta"]},
                    }
                ],
                "pages": [
                    {
                        "id": "overview",
                        "title": "Overview",
                        "sections": [{"id": "main", "widgets": widgets}],
                    }
                ],
            },
        },
    )
    path = f"agents/studio/reports/{report['report_id']}"
    try:
        state = await fixture.primary.request(
            "POST", f"{path}/view-sessions", {"draft_id": report["draft_id"]}
        )
        times, filtered = [], []
        for index in range(20):
            start = perf_counter()
            if index:
                event = await fixture.primary.request(
                    "POST",
                    f"{path}/view-sessions/{state['session_id']}/events",
                    {
                        "event_id": uuid4().hex,
                        "spec_revision": 1,
                        "expected_state_revision": state["state_revision"],
                        "type": "FILTER_SET",
                        "target": "city",
                        "value": {"kind": "single", "values": ["Jakarta"]},
                    },
                )
                state = event["state"]
            result = await fixture.primary.request(
                "POST",
                f"{path}/render",
                {
                    "view_session_id": state["session_id"],
                    "expected_state_revision": state["state_revision"],
                },
            )
            elapsed = perf_counter() - start
            times.append(elapsed)
            if index:
                filtered.append(elapsed)
            assert len(result["widgets"]) == 8
            assert all(widget["rows"] == [["1980.00"]] for widget in result["widgets"])
        measurements = {
            "render_seconds": times,
            "p95_render_seconds": sorted(times)[18],
            "p95_filter_seconds": sorted(filtered)[18],
            "widgets": 8,
            "result_cache": "bypassed: policy fingerprint unavailable",
            "fixture": "patched FE, authenticated caller, governed city revenue",
        }
        (tmp_path / "latency.json").write_text(json.dumps(measurements, indent=2))
        assert measurements["p95_render_seconds"] <= 5
        assert measurements["p95_filter_seconds"] <= 3
    finally:
        await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CONFIG_SMART_REPORT_EVENTS WHERE report_id=%s",
            [report["report_id"]],
        )
