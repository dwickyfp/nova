"""Private versioned templates retaining validated semantic references."""

from fastapi import HTTPException

from app.modules.intelligence.engine_repository import metadata_lock
from app.modules.smart_reports.exports import identity
from app.modules.smart_reports.runtime import report_runtime
from app.modules.smart_reports.schemas import CreateReport, ReportSpecV1
from app.modules.smart_reports.security import require_current_session
from app.modules.smart_reports.service import report_service, stable_id


async def save(report_id: str, session_id: str, revision: int, operation_id: str, user: dict):
    spec, state = await report_service.view(report_id, session_id, user)
    if revision != state.state_revision:
        raise HTTPException(409, "Report view changed; save the current definition")
    await report_runtime.validate(spec, user)
    if any(w.mapping_required for w in spec.widgets().values()):
        raise HTTPException(422, "Map every widget before saving a template")
    template_id = stable_id("template", user["username"], operation_id)
    async with metadata_lock(f"report-template:{template_id}") as lease:
        await report_service.repo.append(
            record_id=template_id,
            report_id=report_id,
            kind="template",
            owner=user["username"],
            operation_id=operation_id,
            expected_revision=0,
            request={
                "report_id": report_id,
                "spec": spec.model_dump(mode="json"),
                "state_revision": revision,
            },
            payload={
                "kind": "template",
                "spec": spec.model_dump(mode="json"),
                "identity": identity(user),
                "report_id": report_id,
            },
            lease=lease,
        )
    return {"template_id": template_id, "title": spec.title}


async def list_templates(user: dict, after: str) -> dict:
    await require_current_session(user)
    ids = await report_service.repo.ids("template", after=after)
    result = []
    for template_id in ids:
        record = await report_service.repo.read(template_id)
        if record and record["payload"].get("identity") == identity(user):
            try:
                await report_service.access(record["payload"]["report_id"], user)
            except HTTPException as exc:
                if exc.status_code in {403, 404}:
                    continue
                raise
            result.append({"template_id": template_id, "title": record["payload"]["spec"]["title"]})
    return {"templates": result, "next_cursor": ids[-1] if len(ids) == 100 else None}


async def instantiate(template_id: str, operation_id: str, user: dict) -> dict:
    await require_current_session(user)
    record = await report_service.repo.read(template_id)
    if (
        not record
        or record["payload"].get("kind") != "template"
        or record["payload"].get("identity") != identity(user)
    ):
        raise HTTPException(404, "Template not found")
    await report_service.access(record["payload"]["report_id"], user)
    # Unknown schema versions and stale semantic sources are rejected by the
    # same creation path; templates cannot smuggle server-owned identity fields.
    spec = ReportSpecV1.model_validate(record["payload"]["spec"])
    return await report_service.create(CreateReport(operation_id=operation_id, spec=spec), user)
