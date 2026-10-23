"""Report lifecycle and authorization composed with Nova's existing owners."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException

from app.common.audit import write_audit_log
from app.modules.assistant.security import session_security
from app.modules.intelligence.engine_repository import metadata_lock
from app.modules.smart_reports.patches import apply_patch
from app.modules.smart_reports.repository import digest, report_repository
from app.modules.smart_reports.runtime import report_runtime
from app.modules.smart_reports.schemas import (
    CreateReport,
    GrantRequest,
    Origin,
    PatchRequest,
    ReportSpecV1,
    ReportViewStateV1,
    ViewCreate,
)
from app.modules.smart_reports.security import require_current_session
from app.modules.smart_reports.sessions import new_session_id, view_sessions


def stable_id(*parts: str) -> str:
    return str(uuid5(NAMESPACE_URL, "nova:smart-reports:" + digest(parts)))


def now() -> str:
    return datetime.now(UTC).isoformat()


class ReportService:
    def __init__(self, repository=report_repository):
        self.repo = repository

    async def access(self, report_id: str, user: dict, action: str = "view") -> dict:
        await require_current_session(user)
        security = session_security(user)
        record = await self.repo.read(report_id)
        if record is None or record["payload"].get("kind") != "report":
            raise HTTPException(404, "Report not found")
        report = record["payload"]
        permission = "owner" if report["owner"] == security.principal else None
        if permission is None:
            for target_type, target in (
                ("user", security.principal),
                ("role", security.active_role),
            ):
                grant = await self.repo.read(stable_id(report_id, "grant", target_type, target))
                if grant and not grant["payload"]["revoked"]:
                    candidate = grant["payload"]["permission"]
                    if (
                        permission is None
                        or {"viewer": 1, "editor": 2, "publisher": 3}[candidate]
                        > {"viewer": 1, "editor": 2, "publisher": 3}[permission]
                    ):
                        permission = candidate
        allowed = {
            "view": {"owner", "viewer", "editor", "publisher"},
            "edit": {"owner", "editor", "publisher"},
            "publish": {"owner", "publisher"},
            "share": {"owner"},
        }
        if permission not in allowed[action] or report.get("archived"):
            await self.audit(report_id, user, action, success=False)
            raise HTTPException(404, "Report not found")
        return {**report, "revision": record["revision"], "permission": permission}

    async def audit(self, report_id: str, user: dict, action: str, *, success: bool = True) -> None:
        await write_audit_log(
            event_type="SMART_REPORT",
            user_name=user["username"],
            action=action.upper(),
            object_type="SMART_REPORT",
            object_name=report_id,
            active_role=user.get("active_role"),
            status="SUCCESS" if success else "DENIED",
        )

    async def origin(self, origin: Origin | None, user: dict) -> None:
        if origin is None:
            return
        from app.modules.assistant.repository import assistant_repository

        thread = await assistant_repository.get_thread(origin.thread_id, user_name=user["username"])
        if not thread or thread.get("agent_id") != origin.agent_id:
            raise HTTPException(404, "Original conversation unavailable")

    async def create(self, body: CreateReport, user: dict) -> dict:
        await require_current_session(user)
        await self.origin(body.origin, user)
        await report_runtime.validate(body.spec, user)
        report_id = stable_id(user["username"], body.operation_id)
        draft_id = stable_id(report_id, "draft", user["username"])
        request = body.model_dump(mode="json")
        async with metadata_lock(f"smart-report:{report_id}") as lease:
            report = {
                "kind": "report",
                "report_id": report_id,
                "owner": user["username"],
                "title": body.spec.title,
                "published_revision": 0,
                "archived": False,
                "created_at": now(),
                "updated_at": now(),
            }
            previous = await self.repo.replay(report_id, body.operation_id, request)
            if not previous:
                await self.repo.append(
                    record_id=report_id,
                    report_id=report_id,
                    kind="report",
                    owner=user["username"],
                    operation_id=body.operation_id,
                    expected_revision=0,
                    payload=report,
                    request=request,
                    lease=lease,
                )
            await self.repo.append(
                record_id=draft_id,
                report_id=report_id,
                kind="draft",
                owner=user["username"],
                operation_id=body.operation_id,
                expected_revision=0,
                payload={
                    "report_id": report_id,
                    "editor": user["username"],
                    "base_published_revision": 0,
                    "spec": body.spec.model_dump(mode="json"),
                    "updated_at": now(),
                },
                request=request,
                lease=lease,
            )
        await self.audit(report_id, user, "create")
        return {"report_id": report_id, "draft_id": draft_id, "draft_revision": 1}

    async def list(self, user: dict, after: str = "") -> dict:
        ids = await self.repo.ids("report", after=after, limit=100)
        reports = []
        for identity in ids:
            try:
                report = await self.access(identity, user)
                draft_id = stable_id(identity, "draft", user["username"])
                draft = await self.repo.read(draft_id)
                if (
                    not report["published_revision"]
                    and draft is None
                    and report["permission"] == "viewer"
                ):
                    continue
                migration = (
                    await self.repo.read(
                        stable_id("dashboard-migration", report["legacy_dashboard_id"])
                    )
                    if report.get("legacy_dashboard_id")
                    else None
                )
                reports.append(
                    {
                        **report,
                        "draft_id": draft_id if draft else None,
                        "migration_status": migration["payload"]["status"] if migration else None,
                        "requires_mapping": any(
                            w.mapping_required
                            for w in ReportSpecV1.model_validate(draft["payload"]["spec"])
                            .widgets()
                            .values()
                        )
                        if draft
                        else False,
                    }
                )
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
        return {"reports": reports, "next_cursor": ids[-1] if len(ids) == 100 else None}

    async def spec(
        self,
        report_id: str,
        user: dict,
        *,
        draft_id: str | None = None,
        revision: int | None = None,
    ) -> tuple[ReportSpecV1, int]:
        report = await self.access(report_id, user, "edit" if draft_id else "view")
        if draft_id:
            record = await self.repo.read(draft_id, revision)
            if (
                not record
                or record["payload"].get("report_id") != report_id
                or record["payload"].get("editor") != user["username"]
            ):
                raise HTTPException(404, "Draft not found")
        else:
            selected = revision or report["published_revision"]
            if not selected or selected > report["published_revision"]:
                raise HTTPException(404, "Published revision not found")
            record = await self.repo.read(stable_id(report_id, "published"), selected)
            if not record:
                raise HTTPException(409, "Published revision requires reconciliation")
        return ReportSpecV1.model_validate(record["payload"]["spec"]), record["revision"]

    async def draft(self, report_id: str, user: dict, operation_id: str) -> dict:
        draft_id = stable_id(report_id, "draft", user["username"])
        async with metadata_lock(f"smart-report:{report_id}") as lease:
            report = await self.access(report_id, user, "edit")
            existing = await self.repo.read(draft_id)
            if existing:
                return {"draft_id": draft_id, **existing}
            if report["published_revision"]:
                spec, revision = await self.spec(report_id, user)
            else:
                initial = await self.repo.read(stable_id(report_id, "draft", report["owner"]), 1)
                if initial is None:
                    raise HTTPException(409, "Initial report draft requires reconciliation")
                spec = ReportSpecV1.model_validate(initial["payload"]["spec"])
                revision = 0
            record = await self.repo.append(
                record_id=draft_id,
                report_id=report_id,
                kind="draft",
                owner=report["owner"],
                operation_id=operation_id,
                expected_revision=0,
                lease=lease,
                request={"base": revision},
                payload={
                    "report_id": report_id,
                    "editor": user["username"],
                    "base_published_revision": revision,
                    "spec": spec.model_dump(mode="json"),
                    "updated_at": now(),
                },
            )
            return {"draft_id": draft_id, **record}

    async def patch(
        self,
        report_id: str,
        draft_id: str,
        body: PatchRequest,
        user: dict,
        *,
        preview: bool = False,
    ) -> dict:
        coordination = nullcontext() if preview else metadata_lock(f"smart-report:{report_id}")
        async with coordination as lease:
            report = await self.access(report_id, user, "edit")
            original, revision = await self.spec(report_id, user, draft_id=draft_id)
            request = body.model_dump(mode="json")
            if not preview:
                replay = await self.repo.replay(draft_id, body.operation_id, request)
                if replay:
                    return replay
            if revision != body.expected_revision:
                raise HTTPException(409, "Draft changed; reload before applying this edit")
            record = await self.repo.read(draft_id, revision)
            assert record is not None
            undo = list(record["payload"].get("undo", [revision - 1] if revision > 1 else []))
            redo = list(record["payload"].get("redo", []))
            operation = body.operations[0]
            if len(body.operations) == 1 and operation.op in {"UNDO_DRAFT", "REDO_DRAFT"}:
                if operation.value or operation.target or operation.parent:
                    raise ValueError("Undo and redo do not accept additional fields")
                source_stack, destination_stack = (
                    (undo, redo) if operation.op == "UNDO_DRAFT" else (redo, undo)
                )
                if not source_stack:
                    raise HTTPException(409, "No draft change is available to undo or redo")
                updated, _ = await self.spec(
                    report_id, user, draft_id=draft_id, revision=source_stack.pop()
                )
                destination_stack.append(revision)
            elif len(body.operations) == 1 and operation.op == "RESTORE_DRAFT":
                value = body.operations[0].value
                if (
                    set(value) != {"revision"}
                    or type(value["revision"]) is not int
                    or not 1 <= value["revision"] < revision
                ):
                    raise ValueError("Choose an earlier draft revision")
                updated, _ = await self.spec(
                    report_id, user, draft_id=draft_id, revision=value["revision"]
                )
            else:
                updated = apply_patch(original, body.operations)
            if operation.op not in {"UNDO_DRAFT", "REDO_DRAFT"}:
                undo.append(revision)
                redo = []
            await report_runtime.validate(updated, user)
            if preview:
                _current, current_revision = await self.spec(report_id, user, draft_id=draft_id)
                if current_revision != revision:
                    raise HTTPException(409, "Draft changed; preview the current revision")
                return {
                    "before": original.model_dump(mode="json"),
                    "after": updated.model_dump(mode="json"),
                    "expected_revision": revision,
                    "preview_hash": digest(updated.model_dump()),
                }
            base = record["payload"]["base_published_revision"]
            published = (
                await self.repo.read(
                    stable_id(report_id, "published"), report["published_revision"]
                )
                if report["published_revision"]
                else None
            )
            if (
                published
                and published["payload"].get("draft_id") == draft_id
                and published["payload"].get("draft_revision") == revision
            ):
                base = report["published_revision"]
            result = await self.repo.append(
                record_id=draft_id,
                report_id=report_id,
                kind="draft",
                owner=report["owner"],
                operation_id=body.operation_id,
                expected_revision=revision,
                lease=lease,
                request=request,
                payload={
                    **record["payload"],
                    "base_published_revision": base,
                    "spec": updated.model_dump(mode="json"),
                    "operations": [op.model_dump(mode="json") for op in body.operations],
                    "undo": undo[-50:],
                    "redo": redo[-50:],
                    "updated_at": now(),
                },
            )
        await self.audit(report_id, user, "edit")
        return result

    async def publish(
        self,
        report_id: str,
        draft_id: str,
        user: dict,
        *,
        operation_id: str,
        expected_revision: int,
        expected_published_revision: int,
    ) -> dict:
        async with metadata_lock(f"smart-report:{report_id}", timeout_seconds=120) as lease:
            report = await self.access(report_id, user, "publish")
            spec, revision = await self.spec(report_id, user, draft_id=draft_id)
            request = {
                "draft_id": draft_id,
                "expected_revision": expected_revision,
                "expected_published_revision": expected_published_revision,
            }
            replay = await self.repo.replay(report_id, operation_id, request)
            if replay:
                return replay
            draft = await self.repo.read(draft_id)
            assert draft is not None
            if (
                revision != expected_revision
                or report["published_revision"] != expected_published_revision
            ):
                raise HTTPException(
                    409, "Draft or published report changed; compare before publishing"
                )
            if draft["payload"]["base_published_revision"] != expected_published_revision:
                raise HTTPException(409, "Draft base changed; rebase before publishing")
            if any(w.mapping_required for w in spec.widgets().values()) or not spec.widgets():
                raise HTTPException(422, "Map all widgets before publishing")
            await report_runtime.validate(spec, user)
            state = ReportViewStateV1(
                session_id=new_session_id(),
                report_id=report_id,
                draft_id=draft_id,
                spec_revision=revision,
                active_page_id=spec.pages[0].id,
                filters={f.id: f.default for f in spec.filters},
            )
            for page in spec.pages:
                state.active_page_id = page.id
                smoke = await report_runtime.render(spec, state, user, [])
                if smoke["status"] != "success":
                    raise HTTPException(422, "Report validation failed; inspect affected widgets")
            await require_current_session(user)
            published = await self.repo.append(
                record_id=stable_id(report_id, "published"),
                report_id=report_id,
                kind="published",
                owner=report["owner"],
                operation_id=operation_id,
                expected_revision=expected_published_revision,
                lease=lease,
                request=request,
                payload={
                    "spec": spec.model_dump(mode="json"),
                    "actor": user["username"],
                    "published_at": now(),
                    "draft_id": draft_id,
                    "draft_revision": revision,
                },
            )
            clean = {k: v for k, v in report.items() if k not in {"permission", "revision"}}
            result = await self.repo.append(
                record_id=report_id,
                report_id=report_id,
                kind="report",
                owner=report["owner"],
                operation_id=operation_id,
                expected_revision=report["revision"],
                lease=lease,
                request=request,
                payload={
                    **clean,
                    "title": spec.title,
                    "published_revision": published["revision"],
                    "updated_at": now(),
                },
            )
        await self.audit(report_id, user, "publish")
        return result

    async def grant(self, report_id: str, body: GrantRequest, user: dict) -> dict:
        async with metadata_lock(f"smart-report:{report_id}") as lease:
            report = await self.access(report_id, user, "share")
            identity = stable_id(report_id, "grant", body.target_type, body.target_name)
            result = await self.repo.append(
                record_id=identity,
                report_id=report_id,
                kind="grant",
                owner=report["owner"],
                operation_id=body.operation_id,
                expected_revision=body.expected_revision,
                lease=lease,
                request=body.model_dump(),
                payload={
                    "report_id": report_id,
                    "target_type": body.target_type,
                    "target_name": body.target_name,
                    "permission": body.permission,
                    "revoked": body.revoked,
                },
            )
        await self.audit(report_id, user, "grant")
        return {"grant_id": identity, **result}

    async def rebase(
        self,
        report_id: str,
        draft_id: str,
        user: dict,
        *,
        operation_id: str,
        expected_revision: int,
        expected_published_revision: int,
        restore_revision: int | None = None,
        review_id: str | None = None,
    ) -> dict:
        request = {
            "expected_revision": expected_revision,
            "expected_published_revision": expected_published_revision,
            "restore_revision": restore_revision,
            "review_id": review_id,
        }
        async with metadata_lock(f"smart-report:{report_id}") as lease:
            report = await self.access(report_id, user, "edit")
            spec, revision = await self.spec(report_id, user, draft_id=draft_id)
            replay = await self.repo.replay(draft_id, operation_id, request)
            if replay:
                return replay
            if (
                revision != expected_revision
                or report["published_revision"] != expected_published_revision
            ):
                raise HTTPException(409, "Draft or published report changed; compare again")
            if restore_revision is not None and review_id is not None:
                raise ValueError("Choose one draft replacement source")
            if restore_revision is not None:
                spec, _ = await self.spec(report_id, user, revision=restore_revision)
            if review_id is not None:
                submitted = await self.review(report_id, review_id, user)
                spec = ReportSpecV1.model_validate(submitted["spec"])
            await report_runtime.validate(spec, user)
            current = await self.repo.read(draft_id)
            assert current is not None
            result = await self.repo.append(
                record_id=draft_id,
                report_id=report_id,
                kind="draft",
                owner=report["owner"],
                operation_id=operation_id,
                expected_revision=revision,
                request=request,
                lease=lease,
                payload={
                    **current["payload"],
                    "spec": spec.model_dump(mode="json"),
                    "base_published_revision": expected_published_revision,
                    "rebase": request,
                    "undo": [*current["payload"].get("undo", []), revision][-50:],
                    "redo": [],
                    "updated_at": now(),
                },
            )
        await self.audit(report_id, user, "restore" if restore_revision else "rebase")
        return result

    async def submit_review(
        self,
        report_id: str,
        draft_id: str,
        user: dict,
        *,
        operation_id: str,
        expected_revision: int,
    ) -> dict:
        identity = stable_id(report_id, "review", draft_id, operation_id)
        request = {"draft_id": draft_id, "expected_revision": expected_revision}
        async with metadata_lock(f"smart-report:{report_id}") as lease:
            report = await self.access(report_id, user, "edit")
            spec, revision = await self.spec(report_id, user, draft_id=draft_id)
            replay = await self.repo.replay(identity, operation_id, request)
            if replay:
                return {"review_id": identity, **replay["payload"]}
            if revision != expected_revision:
                raise HTTPException(409, "Draft changed; submit the current revision")
            record = await self.repo.read(draft_id, revision)
            assert record is not None
            result = await self.repo.append(
                record_id=identity,
                report_id=report_id,
                kind="review",
                owner=report["owner"],
                operation_id=operation_id,
                expected_revision=0,
                request=request,
                lease=lease,
                payload={
                    "kind": "review",
                    "report_id": report_id,
                    "draft_id": draft_id,
                    "draft_revision": revision,
                    "draft_checksum": record["checksum"],
                    "editor": user["username"],
                    "title": spec.title,
                    "submitted_at": now(),
                },
            )
        await self.audit(report_id, user, "submit_review")
        return {"review_id": identity, **result["payload"]}

    async def review(self, report_id: str, review_id: str, user: dict) -> dict:
        await self.access(report_id, user, "publish")
        record = await self.repo.read(review_id)
        if (
            not record
            or record["payload"].get("kind") != "review"
            or record["payload"].get("report_id") != report_id
        ):
            raise HTTPException(404, "Submitted draft not found")
        submitted = record["payload"]
        draft = await self.repo.read(submitted["draft_id"], submitted["draft_revision"])
        if (
            not draft
            or draft["checksum"] != submitted["draft_checksum"]
            or draft["payload"].get("report_id") != report_id
            or draft["payload"].get("editor") != submitted["editor"]
        ):
            raise HTTPException(409, "Submitted draft requires reconciliation")
        spec = ReportSpecV1.model_validate(draft["payload"]["spec"])
        return {"review_id": review_id, **submitted, "spec": spec.model_dump(mode="json")}

    async def archive(
        self,
        report_id: str,
        user: dict,
        *,
        operation_id: str,
        expected_revision: int,
        archived: bool,
    ) -> dict:
        await require_current_session(user)
        request = {"expected_revision": expected_revision, "archived": archived}
        async with metadata_lock(f"smart-report:{report_id}") as lease:
            record = await self.repo.read(report_id)
            if not record or record["payload"].get("owner") != user["username"]:
                raise HTTPException(404, "Report not found")
            result = await self.repo.append(
                record_id=report_id,
                report_id=report_id,
                kind="report",
                owner=user["username"],
                operation_id=operation_id,
                expected_revision=expected_revision,
                request=request,
                lease=lease,
                payload={**record["payload"], "archived": archived, "updated_at": now()},
            )
        await self.audit(report_id, user, "archive" if archived else "unarchive")
        return result

    async def create_view(self, report_id: str, body: ViewCreate, user: dict) -> ReportViewStateV1:
        spec, revision = await self.spec(
            report_id, user, draft_id=body.draft_id, revision=body.revision
        )
        await self.origin(body.origin, user)
        from app.modules.smart_reports.context import bind, bound

        if body.origin:
            existing = await bound(user, body.origin)
            if (
                existing
                and existing.report_id == report_id
                and existing.draft_id == body.draft_id
                and existing.spec_revision == revision
            ):
                return existing
        state = ReportViewStateV1(
            session_id=new_session_id(),
            report_id=report_id,
            draft_id=body.draft_id,
            spec_revision=revision,
            active_page_id=spec.pages[0].id,
            filters={f.id: f.default for f in spec.filters},
            origin=body.origin,
        )
        if body.preset_id:
            preset = await self.repo.read(body.preset_id)
            if (
                not preset
                or preset["payload"].get("owner") != user["username"]
                or preset["payload"].get("active_role") != user["active_role"]
                or preset["payload"].get("report_id") != report_id
            ):
                raise HTTPException(404, "Saved view not found")
            saved = ReportViewStateV1.model_validate(preset["payload"]["state"])
            if saved.spec_revision != revision or saved.draft_id != body.draft_id:
                raise HTTPException(409, "Saved view belongs to a different report revision")
            state = saved.model_copy(
                update={"session_id": state.session_id, "state_revision": 1, "origin": body.origin}
            )
        await view_sessions.create(user, state)
        await bind(user, state)
        return state

    async def view(
        self, report_id: str, session_id: str, user: dict
    ) -> tuple[ReportSpecV1, ReportViewStateV1]:
        state = await view_sessions.get(user, report_id, session_id)
        spec, _ = await self.spec(
            report_id, user, draft_id=state.draft_id, revision=state.spec_revision
        )
        return spec, state

    async def verify_view(self, expected: ReportViewStateV1, user: dict) -> None:
        _spec, current = await self.view(expected.report_id, expected.session_id, user)
        if current != expected:
            raise HTTPException(409, "Report state changed during execution; refresh the view")


report_service = ReportService()
