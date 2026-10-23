"""Adversarial lifecycle tests: draft isolation, crash retry and active-role grants."""

from contextlib import asynccontextmanager
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.smart_reports.repository import digest
from app.modules.smart_reports.schemas import CreateReport, GrantRequest, PatchRequest, ReportPatch
from app.modules.smart_reports.service import ReportService
from tests.unit.test_smart_reports import USER, report_spec


async def test_add_artifact_copies_verified_visual_into_draft_and_preserves_published(
    lifecycle, monkeypatch
):
    from app.modules.smart_reports import artifacts

    reports, repository = lifecycle
    created = await reports.create(CreateReport(operation_id="create", spec=report_spec()), USER)
    report_id, draft_id = created["report_id"], created["draft_id"]
    await reports.publish(
        report_id,
        draft_id,
        USER,
        operation_id="publish",
        expected_revision=1,
        expected_published_revision=0,
    )
    artifact = {
        "artifact_id": "artifact-a",
        "title": "Revenue by city",
        "artifact_type": "chart",
        "sql_text": "SELECT region, SUM(amount) FROM sales GROUP BY region",
        "chart_spec": {
            "mark": "bar",
            "encoding": {"x": {"field": "region"}, "y": {"field": "total_revenue"}},
        },
    }
    spec = report_spec()
    proof = {
        "artifact_fingerprint": artifacts.artifact_fingerprint(artifact),
        "source": spec.sources[0].model_dump(),
        "query": spec.widgets()["cities"].query.model_dump(),
    }
    await repository.append(
        record_id=artifacts.stable_id("artifact-binding", "artifact-a"),
        operation_id="proof",
        request=proof,
        payload=proof,
        kind="artifact_binding",
        expected_revision=0,
    )
    monkeypatch.setattr(artifacts, "report_service", reports)
    monkeypatch.setattr(artifacts, "report_repository", repository)
    get = AsyncMock(return_value=artifact)
    monkeypatch.setattr(artifacts.artifact_repository, "get", get)
    added = await artifacts.add_to_report(report_id, draft_id, "artifact-a", "main", "add", 1, USER)
    get.assert_awaited_with("artifact-a", owner_name="alice")
    assert added["requires_mapping"] is False
    assert (
        await artifacts.add_to_report(report_id, draft_id, "artifact-a", "main", "add", 1, USER)
        == added
    )
    assert len((await reports.spec(report_id, USER))[0].widgets()) == 2
    copied = (await reports.spec(report_id, USER, draft_id=draft_id))[0].widgets()[
        added["widget_id"]
    ]
    assert copied.kind == "bar" and copied.encoding.x == "region"
    artifact["chart_spec"]["mark"] = "line"
    assert (await reports.spec(report_id, USER, draft_id=draft_id))[0].widgets()[
        added["widget_id"]
    ] == copied
    unmapped = await artifacts.add_to_report(
        report_id, draft_id, "artifact-a", "main", "add-changed", 2, USER
    )
    assert unmapped["requires_mapping"] is True
    assert (await reports.spec(report_id, USER, draft_id=draft_id))[0].widgets()[
        unmapped["widget_id"]
    ].query is None
    with pytest.raises(HTTPException) as mapping:
        await reports.publish(
            report_id,
            draft_id,
            USER,
            operation_id="publish-unmapped",
            expected_revision=3,
            expected_published_revision=1,
        )
    assert mapping.value.status_code == 422


@pytest.mark.parametrize("change", [None, "revision", "security"])
async def test_candidate_preview_has_no_write_and_rechecks_revision_and_security(
    lifecycle, monkeypatch, change
):
    from app.modules.smart_reports import router, service

    reports, repository = lifecycle
    monkeypatch.setattr(router, "report_service", reports)
    created = await reports.create(CreateReport(operation_id="create", spec=report_spec()), USER)
    report_id, draft_id = created["report_id"], created["draft_id"]
    records = deepcopy(repository.records)
    request = router.PreviewRenderRequest(
        patch=PatchRequest(
            operation_id="candidate",
            expected_revision=1,
            operations=[ReportPatch(op="SET_REPORT_METADATA", value={"title": "Candidate"})],
        )
    )

    async def render(spec, state, user, widget_ids):
        assert spec.title == "Candidate"
        assert state.filters["city"].values == ["Jakarta"]
        assert user == USER
        assert (await reports.spec(report_id, USER, draft_id=draft_id))[0].title == "City revenue"
        if change == "revision":
            await reports.patch(
                report_id,
                draft_id,
                request.patch.model_copy(update={"operation_id": "concurrent"}),
                USER,
            )
        elif change == "security":
            monkeypatch.setattr(
                service,
                "require_current_session",
                AsyncMock(side_effect=HTTPException(409, "Security context changed")),
            )
        return {"status": "success", "widgets": []}

    monkeypatch.setattr(router.report_runtime, "render", render)
    connection = AsyncMock()
    connection.is_disconnected.return_value = False
    if change:
        with pytest.raises(HTTPException) as conflict:
            await router.preview_render(report_id, draft_id, request, USER, connection)
        assert conflict.value.status_code == 409
    else:
        preview = await router.preview_render(report_id, draft_id, request, USER, connection)
        assert preview["spec"].title == "Candidate"
        assert preview["state"].draft_id == draft_id
        assert repository.records == records
        with pytest.raises(HTTPException) as invalid:
            await router.preview_render(
                report_id,
                draft_id,
                request.model_copy(update={"page_id": "forged"}),
                USER,
                connection,
            )
        assert invalid.value.status_code == 422


async def test_patch_preview_rejects_a_revision_changed_during_validation(lifecycle, monkeypatch):
    from app.modules.smart_reports import service

    reports, repository = lifecycle
    created = await reports.create(CreateReport(operation_id="create", spec=report_spec()), USER)
    report_id, draft_id = created["report_id"], created["draft_id"]

    async def concurrent_write(*args):
        record = deepcopy(repository.records[draft_id][-1])
        record["revision"] = 2
        repository.records[draft_id].append(record)

    monkeypatch.setattr(service.report_runtime, "validate", concurrent_write)
    with pytest.raises(HTTPException) as changed:
        await reports.patch(
            report_id, draft_id,
            PatchRequest(
                operation_id="preview", expected_revision=1,
                operations=[ReportPatch(op="SET_REPORT_METADATA", value={"title": "Candidate"})],
            ),
            USER, preview=True,
        )
    assert changed.value.status_code == 409
    assert len(repository.records[draft_id]) == 2
    assert repository.records[draft_id][-1]["payload"]["spec"]["title"] == "City revenue"


async def test_undo_redo_append_history_clear_branch_and_keep_published(lifecycle):
    reports, repository = lifecycle
    created = await reports.create(CreateReport(operation_id="create", spec=report_spec()), USER)
    report_id, draft_id = created["report_id"], created["draft_id"]
    await reports.publish(
        report_id,
        draft_id,
        USER,
        operation_id="publish",
        expected_revision=1,
        expected_published_revision=0,
    )

    async def edit(op, revision, value=None, preview=False):
        return await reports.patch(
            report_id,
            draft_id,
            PatchRequest(
                operation_id=f"{op}-{revision}",
                expected_revision=revision,
                operations=[ReportPatch(op=op, value=value or {})],
            ),
            USER,
            preview=preview,
        )

    await edit("SET_REPORT_METADATA", 1, {"title": "Draft A"})
    await edit("SET_REPORT_METADATA", 2, {"title": "Draft B"})
    immutable = deepcopy(repository.records[draft_id])
    assert (await edit("UNDO_DRAFT", 3, preview=True))["after"]["title"] == "Draft A"
    assert repository.records[draft_id] == immutable
    undone = await edit("UNDO_DRAFT", 3)
    assert undone["revision"] == 4
    assert undone["payload"]["spec"]["title"] == "Draft A"
    assert await edit("UNDO_DRAFT", 3) == undone
    with pytest.raises(HTTPException) as stale:
        await edit("REDO_DRAFT", 3)
    assert stale.value.status_code == 409
    assert (await edit("UNDO_DRAFT", 4))["payload"]["spec"]["title"] == "City revenue"
    assert (await edit("REDO_DRAFT", 5))["payload"]["spec"]["title"] == "Draft A"
    assert (await edit("REDO_DRAFT", 6))["payload"]["spec"]["title"] == "Draft B"
    await edit("UNDO_DRAFT", 7)
    await edit("SET_REPORT_METADATA", 8, {"title": "Different branch"})
    with pytest.raises(HTTPException) as exhausted:
        await edit("REDO_DRAFT", 9)
    assert exhausted.value.status_code == 409
    assert repository.records[draft_id][:3] == immutable
    assert (await reports.spec(report_id, USER))[0].title == "City revenue"
    with pytest.raises(ValueError):
        await edit("UNDO_DRAFT", 9, {"revision": 1})


async def test_submitted_revision_is_pinned_and_requires_publisher_copy_then_publish(lifecycle):
    reports, repository = lifecycle
    created = await reports.create(CreateReport(operation_id="create", spec=report_spec()), USER)
    report_id, owner_draft = created["report_id"], created["draft_id"]
    for username, permission in [("editor", "editor"), ("publisher", "publisher")]:
        await reports.grant(
            report_id,
            GrantRequest(
                operation_id=f"grant-{username}",
                expected_revision=0,
                target_type="user",
                target_name=username,
                permission=permission,
            ),
            USER,
        )
    await reports.patch(
        report_id,
        owner_draft,
        PatchRequest(
            operation_id="owner-edit",
            expected_revision=1,
            operations=[
                ReportPatch(op="SET_REPORT_METADATA", value={"title": "Private owner edit"})
            ],
        ),
        USER,
    )
    editor, publisher = {**USER, "username": "editor"}, {**USER, "username": "publisher"}
    editor_draft = (await reports.draft(report_id, editor, "start"))["draft_id"]
    assert (await reports.spec(report_id, editor, draft_id=editor_draft))[0].title == "City revenue"
    await reports.patch(
        report_id,
        editor_draft,
        PatchRequest(
            operation_id="editor-edit",
            expected_revision=1,
            operations=[ReportPatch(op="SET_REPORT_METADATA", value={"title": "Ready for review"})],
        ),
        editor,
    )
    submitted = await reports.submit_review(
        report_id, editor_draft, editor, operation_id="submit", expected_revision=2
    )
    await reports.patch(
        report_id,
        editor_draft,
        PatchRequest(
            operation_id="private-edit",
            expected_revision=2,
            operations=[
                ReportPatch(op="SET_REPORT_METADATA", value={"title": "Unsubmitted private edit"})
            ],
        ),
        editor,
    )
    assert (
        await reports.submit_review(
            report_id, editor_draft, editor, operation_id="submit", expected_revision=2
        )
        == submitted
    )
    with pytest.raises(HTTPException) as stale:
        await reports.submit_review(
            report_id, editor_draft, editor, operation_id="stale", expected_revision=2
        )
    assert stale.value.status_code == 409
    for user in [publisher, USER]:
        with pytest.raises(HTTPException):
            await reports.spec(report_id, user, draft_id=editor_draft)
        assert (await reports.review(report_id, submitted["review_id"], user))["spec"][
            "title"
        ] == "Ready for review"
    with pytest.raises(HTTPException):
        await reports.review(report_id, submitted["review_id"], editor)
    publisher_draft = (await reports.draft(report_id, publisher, "publisher-start"))["draft_id"]
    copied = await reports.rebase(
        report_id,
        publisher_draft,
        publisher,
        operation_id="copy",
        expected_revision=1,
        expected_published_revision=0,
        review_id=submitted["review_id"],
    )
    assert copied["payload"]["spec"]["title"] == "Ready for review"
    assert (await reports.access(report_id, publisher))["published_revision"] == 0
    await reports.publish(
        report_id,
        publisher_draft,
        publisher,
        operation_id="release",
        expected_revision=2,
        expected_published_revision=0,
    )
    assert (await reports.spec(report_id, USER))[0].title == "Ready for review"
    repository.records[editor_draft][1]["checksum"] = "corrupted"
    with pytest.raises(HTTPException) as corrupt:
        await reports.review(report_id, submitted["review_id"], publisher)
    assert corrupt.value.status_code == 409


class MemoryRecords:
    def __init__(self):
        self.records = {}
        self.operations = {}
        self.fail_pointer_once = False

    async def read(self, identity, revision=None):
        history = self.records.get(identity, [])
        return deepcopy(history[revision - 1] if revision else history[-1]) if history else None

    async def replay(self, identity, operation, request):
        previous = self.operations.get((identity, operation))
        if previous:
            fingerprint, result = previous
            if fingerprint != digest(request):
                raise HTTPException(409, "operation collision")
            return deepcopy(result)

    async def append(self, **kwargs):
        identity = kwargs["record_id"]
        replay = await self.replay(identity, kwargs["operation_id"], kwargs["request"])
        if replay:
            return replay
        history = self.records.setdefault(identity, [])
        if len(history) != kwargs["expected_revision"]:
            raise HTTPException(409, "revision conflict")
        if self.fail_pointer_once and kwargs["kind"] == "report":
            self.fail_pointer_once = False
            raise TimeoutError("simulated lost pointer write")
        result = {"revision": len(history) + 1, "payload": deepcopy(kwargs["payload"])}
        result["checksum"] = digest(result["payload"])
        history.append(result)
        self.operations[(identity, kwargs["operation_id"])] = (digest(kwargs["request"]), result)
        return deepcopy(result)


@pytest.fixture
def lifecycle(monkeypatch):
    from app.modules.smart_reports import service

    @asynccontextmanager
    async def lease(*args, **kwargs):
        yield AsyncMock()

    monkeypatch.setattr(service, "metadata_lock", lease)
    monkeypatch.setattr(service, "require_current_session", AsyncMock())
    monkeypatch.setattr(service.report_runtime, "validate", AsyncMock())
    monkeypatch.setattr(
        service.report_runtime, "render", AsyncMock(return_value={"status": "success"})
    )
    repository = MemoryRecords()
    reports = ReportService(repository)
    reports.audit = AsyncMock()
    return reports, repository


async def test_crash_after_version_write_retries_without_duplicate_and_keeps_draft_isolated(
    lifecycle,
):
    reports, repository = lifecycle
    created = await reports.create(CreateReport(operation_id="create", spec=report_spec()), USER)
    report_id, draft_id = created["report_id"], created["draft_id"]
    repository.fail_pointer_once = True
    params = dict(operation_id="publish", expected_revision=1, expected_published_revision=0)
    with pytest.raises(TimeoutError):
        await reports.publish(report_id, draft_id, USER, **params)
    assert (await reports.access(report_id, USER))["published_revision"] == 0
    first = await reports.publish(report_id, draft_id, USER, **params)
    assert first["payload"]["published_revision"] == 1
    assert await reports.publish(report_id, draft_id, USER, **params) == first
    patch = PatchRequest(
        operation_id="edit",
        expected_revision=1,
        operations=[
            ReportPatch(op="SET_REPORT_METADATA", value={"title": "Edited draft"}),
        ],
    )
    updated = await reports.patch(report_id, draft_id, patch, USER)
    assert updated["revision"] == 2
    assert (await reports.spec(report_id, USER))[0].title == "City revenue"
    assert (await reports.spec(report_id, USER, draft_id=draft_id))[0].title == "Edited draft"
    assert await reports.patch(report_id, draft_id, patch, USER) == updated
    with pytest.raises(HTTPException) as stale:
        await reports.publish(
            report_id,
            draft_id,
            USER,
            operation_id="stale",
            expected_revision=1,
            expected_published_revision=1,
        )
    assert stale.value.status_code == 409
    await reports.publish(
        report_id,
        draft_id,
        USER,
        operation_id="publish2",
        expected_revision=2,
        expected_published_revision=1,
    )
    assert (await reports.spec(report_id, USER, revision=1))[0].title == "City revenue"
    assert (await reports.spec(report_id, USER))[0].title == "Edited draft"


async def test_role_grants_use_only_active_role_and_revoke_immediately(lifecycle):
    reports, _ = lifecycle
    created = await reports.create(CreateReport(operation_id="create", spec=report_spec()), USER)
    report_id = created["report_id"]
    grant = GrantRequest(
        operation_id="grant",
        expected_revision=0,
        target_type="role",
        target_name="publisher",
        permission="publisher",
    )
    await reports.grant(report_id, grant, USER)
    bob = {**USER, "username": "bob"}
    with pytest.raises(HTTPException) as inactive:
        await reports.access(report_id, bob)
    assert inactive.value.status_code == 404
    bob["active_role"] = "publisher"
    assert (await reports.access(report_id, bob, "publish"))["permission"] == "publisher"
    await reports.grant(
        report_id,
        grant.model_copy(
            update={
                "operation_id": "revoke",
                "expected_revision": 1,
                "revoked": True,
            }
        ),
        USER,
    )
    with pytest.raises(HTTPException):
        await reports.access(report_id, bob)


async def test_operation_id_cannot_be_reused_for_another_definition(lifecycle):
    reports, _ = lifecycle
    request = CreateReport(operation_id="create", spec=report_spec())
    await reports.create(request, USER)
    changed = request.model_copy(
        update={"spec": request.spec.model_copy(update={"title": "Other"})}
    )
    with pytest.raises(HTTPException) as collision:
        await reports.create(changed, USER)
    assert collision.value.status_code == 409


async def test_collaborating_draft_requires_explicit_rebase_and_restore_appends(lifecycle):
    reports, _ = lifecycle
    created = await reports.create(CreateReport(operation_id="create", spec=report_spec()), USER)
    report_id, alice_draft = created["report_id"], created["draft_id"]
    await reports.publish(
        report_id,
        alice_draft,
        USER,
        operation_id="initial",
        expected_revision=1,
        expected_published_revision=0,
    )
    await reports.grant(
        report_id,
        GrantRequest(
            operation_id="share",
            expected_revision=0,
            target_type="user",
            target_name="bob",
            permission="publisher",
        ),
        USER,
    )
    bob = {**USER, "username": "bob"}
    bob_draft = (await reports.draft(report_id, bob, "bob-draft"))["draft_id"]
    for draft, user, title in [(alice_draft, USER, "Alice edit"), (bob_draft, bob, "Bob edit")]:
        await reports.patch(
            report_id,
            draft,
            PatchRequest(
                operation_id="edit",
                expected_revision=1,
                operations=[ReportPatch(op="SET_REPORT_METADATA", value={"title": title})],
            ),
            user,
        )
    await reports.publish(
        report_id,
        alice_draft,
        USER,
        operation_id="alice-release",
        expected_revision=2,
        expected_published_revision=1,
    )
    with pytest.raises(HTTPException) as conflict:
        await reports.publish(
            report_id,
            bob_draft,
            bob,
            operation_id="bob-release",
            expected_revision=2,
            expected_published_revision=2,
        )
    assert conflict.value.status_code == 409
    rebased = await reports.rebase(
        report_id,
        bob_draft,
        bob,
        operation_id="rebase",
        expected_revision=2,
        expected_published_revision=2,
    )
    assert rebased["payload"]["spec"]["title"] == "Bob edit"
    await reports.publish(
        report_id,
        bob_draft,
        bob,
        operation_id="bob-release",
        expected_revision=3,
        expected_published_revision=2,
    )
    restored = await reports.rebase(
        report_id,
        bob_draft,
        bob,
        operation_id="restore",
        expected_revision=3,
        expected_published_revision=3,
        restore_revision=1,
    )
    assert restored["revision"] == 4
    assert (await reports.spec(report_id, bob))[0].title == "Bob edit"
    await reports.publish(
        report_id,
        bob_draft,
        bob,
        operation_id="restored-release",
        expected_revision=4,
        expected_published_revision=3,
    )
    assert (await reports.spec(report_id, bob))[1] == 4
    assert (await reports.spec(report_id, bob, revision=2))[0].title == "Alice edit"


async def test_archive_is_owner_only_reversible_and_revision_checked(lifecycle):
    reports, _ = lifecycle
    created = await reports.create(CreateReport(operation_id="create", spec=report_spec()), USER)
    identity = created["report_id"]
    with pytest.raises(HTTPException):
        await reports.archive(
            identity,
            {**USER, "username": "bob"},
            operation_id="forged",
            expected_revision=1,
            archived=True,
        )
    await reports.archive(
        identity, USER, operation_id="archive", expected_revision=1, archived=True
    )
    with pytest.raises(HTTPException):
        await reports.access(identity, USER)
    await reports.archive(
        identity, USER, operation_id="restore", expected_revision=2, archived=False
    )
    assert (await reports.access(identity, USER))["revision"] == 3
