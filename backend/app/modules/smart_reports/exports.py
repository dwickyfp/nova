"""Immutable full-report snapshots, authorized again before every download."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import signal
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException

from app.core.config import load_nova_app_config, settings
from app.modules.intelligence.engine_repository import metadata_lock
from app.modules.ml_engine.artifacts.store import ObjectArtifactStore
from app.modules.query.admission import QueryCapacityError, audit_refusal, query_admission
from app.modules.smart_reports.export_formats import slides, workbook
from app.modules.smart_reports.repository import digest
from app.modules.smart_reports.runtime import report_runtime
from app.modules.smart_reports.schemas import ReportSpecV1, ReportViewStateV1
from app.modules.smart_reports.service import report_service, stable_id


def store() -> ObjectArtifactStore:
    return ObjectArtifactStore(load_nova_app_config().workspace.storage_connection)


def identity(user: dict) -> dict:
    return {key: user[key] for key in ("username", "active_role", "security_context_version")}


def evidence(snapshot: dict) -> str:
    return digest(
        [
            [
                {key: widget[key] for key in ("widget_id", "status", "columns", "rows", "totals")}
                for widget in page["result"]["widgets"]
            ]
            for page in snapshot["pages"]
        ]
    )


async def collect(spec: ReportSpecV1, state: ReportViewStateV1, user: dict, *, as_of=None) -> dict:
    instant = as_of or datetime.now(UTC)
    pages = []
    total_rows = 0
    for page in spec.pages:
        current = state.model_copy(
            deep=True, update={"active_page_id": page.id, "page_index": {}, "page_size": 200}
        )
        result = await report_runtime.render(spec, current, user, [], as_of=instant)
        for widget in result["widgets"]:
            if widget["status"] not in {"success", "empty"} or widget["completeness"] in {
                "error",
                "truncated",
            }:
                raise HTTPException(
                    422,
                    "All widgets must be complete before export; "
                    "refine filters or resolve unavailable data",
                )
            index = 0
            while widget["has_more"]:
                index += 1
                current.page_index[widget["widget_id"]] = index
                continuation = await report_runtime.render(
                    spec, current, user, [widget["widget_id"]], as_of=instant
                )
                more = continuation["widgets"][0]
                if more["status"] not in {"success", "empty"} or more["completeness"] == "error":
                    raise HTTPException(
                        422, "Report pagination failed; no partial export was created"
                    )
                widget["rows"].extend(more["rows"])
                widget["row_kinds"].extend(more["row_kinds"])
                widget["has_more"] = more["has_more"]
                if len(widget["rows"]) > 4999:
                    raise HTTPException(
                        422, "A report table exceeds 4,999 rows; refine filters before export"
                    )
            widget["completeness"] = "complete"
            total_rows += len(widget["rows"])
            if total_rows > 20000:
                raise HTTPException(422, "Report export exceeds 20,000 rows; refine filters")
        current.page_index = {}
        pages.append({"state": current.model_dump(mode="json"), "result": result})
    await report_service.access(state.report_id, user)
    return {"spec": spec.model_dump(mode="json"), "pages": pages}


async def render_snapshot(snapshot: dict) -> dict:
    script = Path(__file__).resolve().parents[4] / "frontend/scripts/render-report-snapshot.mjs"
    # Repository and container layout both keep frontend beside backend.
    if not script.exists():
        script = Path(__file__).resolve().parents[5] / "frontend/scripts/render-report-snapshot.mjs"
    process = await asyncio.create_subprocess_exec(
        "node",
        str(script),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=100 * 1024 * 1024,
        start_new_session=True,
    )
    try:
        async with asyncio.timeout(90):
            output, _error = await process.communicate(json.dumps(snapshot).encode())
        if process.returncode:
            raise HTTPException(
                503, "Snapshot renderer unavailable; check the frontend build and Chromium runtime"
            )
        return json.loads(output)
    finally:
        if process.returncode is None:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            await process.wait()


async def create_export(
    report_id: str, session_id: str, state_revision: int, fmt: str, operation_id: str, user: dict
) -> dict:
    spec, state = await report_service.view(report_id, session_id, user)
    if state.state_revision != state_revision:
        raise HTTPException(409, "Report state changed; retry export from the current view")
    record_id = stable_id(report_id, "export", digest(identity(user)), operation_id)
    request = {"state": state.model_dump(mode="json"), "format": fmt}
    async with metadata_lock(f"report-export:{record_id}", timeout_seconds=120) as lease:
        previous = await report_service.repo.replay(record_id, operation_id, request)
        if previous:
            return public_manifest(record_id, previous["payload"])
        snapshot = await collect(spec, state, user)
        payload = await encode(snapshot, fmt, user)
        await report_service.verify_view(state, user)
        storage = store()
        prefix = f"smart-reports/{record_id}"
        snapshot_uri = await asyncio.to_thread(
            storage.put, f"{prefix}/snapshot.json", json.dumps(snapshot).encode()
        )
        uri = await asyncio.to_thread(storage.put, f"{prefix}/report.{fmt}", payload)
        await report_service.verify_view(state, user)
        manifest = {
            "kind": "export",
            "identity": identity(user),
            "report_id": report_id,
            "state": request["state"],
            "format": fmt,
            "title": spec.title,
            "as_of": snapshot["pages"][0]["result"]["as_of"],
            "page_count": len(spec.pages),
            "size": len(payload),
            "evidence": evidence(snapshot),
            "checksum": hashlib.sha256(payload).hexdigest(),
            "uri": uri,
            "snapshot_uri": snapshot_uri,
        }
        record = await report_service.repo.append(
            record_id=record_id,
            report_id=report_id,
            kind="export",
            owner=user["username"],
            operation_id=operation_id,
            expected_revision=0,
            payload=manifest,
            request=request,
            lease=lease,
        )
    await report_service.audit(report_id, user, "export")
    return public_manifest(record_id, record["payload"])


async def encode(snapshot: dict, fmt: str, user: dict) -> bytes:
    try:
        async with query_admission.slot(
            "report_exports", source_limit=settings.REPORT_EXPORT_MAX_CONCURRENCY,
        ):
            if fmt == "xlsx":
                return workbook(snapshot)
            rendered = await render_snapshot(snapshot)
            return (
                base64.b64decode(rendered["pdf"], validate=True)
                if fmt == "pdf" else slides(rendered)
            )
    except QueryCapacityError:
        await audit_refusal(
            username=user["username"], source="report_exports", target="snapshot",
            session_id=user.get("session_id"),
        )
        raise HTTPException(
            429, "Report export capacity reached. Retry shortly.", headers={"Retry-After": "1"},
        ) from None


def public_manifest(record_id: str, manifest: dict) -> dict:
    return {
        "export_id": record_id,
        **{
            key: manifest[key]
            for key in ("format", "title", "as_of", "page_count", "size", "checksum")
        },
    }


async def download(report_id: str, export_id: str, user: dict) -> tuple[bytes, str]:
    await report_service.access(report_id, user)
    record = await report_service.repo.read(export_id)
    if (
        not record
        or record["payload"].get("kind") != "export"
        or record["payload"].get("report_id") != report_id
        or record["payload"].get("identity") != identity(user)
    ):
        raise HTTPException(404, "Export not found")
    manifest = record["payload"]
    state = ReportViewStateV1.model_validate(manifest["state"])
    spec, _ = await report_service.spec(
        report_id, user, draft_id=state.draft_id, revision=state.spec_revision
    )
    # There is no execution-confirmed policy fingerprint. Re-execution is the
    # fail-closed authorization proof for this historical payload, including masks.
    current = await collect(spec, state, user, as_of=datetime.fromisoformat(manifest["as_of"]))
    if evidence(current) != manifest["evidence"]:
        raise HTTPException(409, "Accessible data changed; create a fresh export")
    payload = await asyncio.to_thread(store().get, manifest["uri"])
    if hashlib.sha256(payload).hexdigest() != manifest["checksum"]:
        raise HTTPException(409, "Export integrity check failed")
    await report_service.access(report_id, user)
    await report_service.audit(report_id, user, "download")
    return payload, manifest["format"]
