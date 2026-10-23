import asyncio
import base64
import io
import zipfile
from copy import deepcopy
from unittest.mock import AsyncMock
from xml.etree import ElementTree

import pytest
from fastapi import HTTPException

from app.modules.query.admission import QueryAdmission
from app.modules.smart_reports import exports
from app.modules.smart_reports.export_formats import slides, workbook
from app.modules.smart_reports.schemas import WidgetResult
from tests.unit.test_smart_reports import USER, report_spec, view


def snapshot():
    spec = report_spec()
    state = view(spec)
    result = WidgetResult(
        widget_id="cities",
        status="success",
        columns=["city", "total_revenue"],
        rows=[["=1+1", "9007199254740992.01"]],
        totals={"total_revenue": "9007199254740992.01"},
    )
    page = {
        "state": state.model_dump(mode="json"),
        "result": {
            "as_of": "2026-10-23T00:00:00+00:00",
            "widgets": [result.model_dump(mode="json")],
        },
    }
    return {"spec": spec.model_dump(mode="json"), "pages": [page, deepcopy(page)]}


async def test_export_burst_refuses_excess_and_releases_cancelled_renderer(monkeypatch):
    gate = QueryAdmission()
    monkeypatch.setattr(exports, "query_admission", gate)
    monkeypatch.setattr(exports.settings, "REPORT_EXPORT_MAX_CONCURRENCY", 2)
    monkeypatch.setattr(exports.settings, "QUERY_MAX_CONCURRENCY", 64)
    started, finish = asyncio.Event(), asyncio.Event()
    running = 0

    async def render(_snapshot):
        nonlocal running
        running += 1
        if running == 2:
            started.set()
        try:
            await finish.wait()
            return {"pdf": base64.b64encode(b"complete-pdf").decode()}
        finally:
            running -= 1

    monkeypatch.setattr(exports, "render_snapshot", render)
    audit = AsyncMock()
    monkeypatch.setattr(exports, "audit_refusal", audit)
    first = asyncio.create_task(exports.encode({}, "pdf", USER))
    second = asyncio.create_task(exports.encode({}, "pdf", USER))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        with pytest.raises(HTTPException) as refused:
            await exports.encode({}, "pdf", USER)
        assert refused.value.status_code == 429
        assert refused.value.headers == {"Retry-After": "1"}
        assert running == gate.active == 2
        assert audit.await_args.kwargs["username"] == USER["username"]
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert running == gate.active == 1
        finish.set()
        assert await second == b"complete-pdf"
        assert await exports.encode({}, "pdf", USER) == b"complete-pdf"
        assert gate.active == 0
    finally:
        finish.set()
        for task in (first, second):
            if not task.done():
                task.cancel()
        await asyncio.gather(first, second, return_exceptions=True)


def test_workbook_includes_every_page_preserves_exact_decimals_and_escapes_formulas():
    result = snapshot()
    result["spec"]["title"] = 'Revenue "A" & B'
    body = workbook(result)
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        for name in archive.namelist():
            if name.endswith((".xml", ".rels")):
                ElementTree.fromstring(archive.read(name))
        sheet = archive.read("xl/worksheets/sheet3.xml").decode()
        assert "9007199254740992.01" in sheet and "=1+1" in sheet
        assert "<f>" not in sheet and 't="inlineStr"' in sheet
        assert archive.read("xl/workbook.xml").count(b"<sheet ") == 3


def test_presentation_keeps_all_rendered_pages_and_image_relationships():
    png = base64.b64encode(b"verified-png-seam").decode()
    result = slides(
        {"images": [{"page": i, "png": png, "width": 1200, "height": 675} for i in range(8)]}
    )
    with zipfile.ZipFile(io.BytesIO(result)) as archive:
        for name in archive.namelist():
            if name.endswith((".xml", ".rels")):
                ElementTree.fromstring(archive.read(name))
        assert len([name for name in archive.namelist() if name.startswith("ppt/media/")]) == 8
        assert archive.read("ppt/presentation.xml").count(b"<p:sldId ") == 8
        assert b"Report page 8" in archive.read("ppt/slides/slide8.xml")


async def test_collect_uses_one_instant_and_refuses_silent_partial_export(monkeypatch):
    spec, state = report_spec(), view()
    good = WidgetResult(
        widget_id="cities",
        status="success",
        completeness="paged",
        columns=["region"],
        rows=[["Jakarta"]],
        has_more=True,
    )
    last = good.model_copy(update={"rows": [["Bandung"]], "has_more": False})
    render = AsyncMock(
        side_effect=[
            {"widgets": [good.model_dump(mode="json")]},
            {"widgets": [last.model_dump(mode="json")]},
        ]
    )
    monkeypatch.setattr(exports.report_runtime, "render", render)
    monkeypatch.setattr(exports.report_service, "access", AsyncMock())
    result = await exports.collect(spec, state, USER)
    assert result["pages"][0]["result"]["widgets"][0]["rows"] == [["Jakarta"], ["Bandung"]]
    assert len({call.kwargs["as_of"] for call in render.await_args_list}) == 1
    render.side_effect = None
    render.return_value = {
        "widgets": [good.model_copy(update={"completeness": "truncated"}).model_dump(mode="json")]
    }
    with pytest.raises(HTTPException) as failed:
        await exports.collect(spec, state, USER)
    assert failed.value.status_code == 422


async def test_download_rechecks_identity_and_current_policy_evidence(monkeypatch):
    current = snapshot()
    state = view()
    manifest = {
        "kind": "export",
        "report_id": state.report_id,
        "identity": exports.identity(USER),
        "state": state.model_dump(mode="json"),
        "as_of": "2026-10-23T00:00:00+00:00",
        "evidence": "old-policy-evidence",
    }
    monkeypatch.setattr(exports.report_service, "access", AsyncMock())
    monkeypatch.setattr(
        exports.report_service.repo, "read", AsyncMock(return_value={"payload": manifest})
    )
    monkeypatch.setattr(exports.report_service, "spec", AsyncMock(return_value=(report_spec(), 1)))
    monkeypatch.setattr(exports, "collect", AsyncMock(return_value=current))
    with pytest.raises(HTTPException) as changed:
        await exports.download(state.report_id, "export", USER)
    assert changed.value.status_code == 409
    with pytest.raises(HTTPException) as other:
        await exports.download(state.report_id, "export", {**USER, "username": "bob"})
    assert other.value.status_code == 404
