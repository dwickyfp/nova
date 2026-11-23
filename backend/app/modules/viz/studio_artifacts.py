"""Chart artifacts in a Studio answer: edit before saving, suggest, then save.

A native chart the agent draws in an answer (a ``DatavizChart`` registry
component with a signed ``draft_ref``, :mod:`app.modules.viz.studio_drafts`)
is also an *artifact*: the turn emits ``artifact.draft`` for it and, once per
turn, ``save.suggested``. Nothing is saved until the person confirms a target
dashboard (docs/arch-19-studio-dataviz.md, "Chart artifacts").

Where the artifact's later history lives
    The turn's own events stay in the message's stored event journal. What
    happens after the turn (a new revision edited in the side panel, a save)
    is appended to one per-message, per-role log in the existing interactive
    state of that message (``config_assistant_ui_state``), under its revision
    CAS. The log holds only chart definitions and save receipts, never rows or
    SQL. A thread read appends the log to the message's journal, numbered after
    its last event, so a reload rebuilds exactly what the live edit or save
    produced. Message steps themselves are append-only for the web role and
    are not rewritten.

Authorization
    Every action runs as the caller. A draft resolves only for its owner under
    the role and session it was made in. ``can_save`` additionally needs the
    active role to read the governed statement now (a zero-row inspection as
    the caller); otherwise the capability is ``role_cannot_author`` with the
    plain reason. Saving re-checks all of that server-side, whatever the client
    shows, and the dashboard's own edit ACL decides an existing target.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid5

from app.core.exceptions import NovaException

from . import studio_drafts
from .contracts.charts import ChartDefinition
from .studio_drafts import DRAFT_REVISION, DraftError, ResolvedDraft
from .studio_publish import (
    PublishError,
    Target,
    _explain,
    operation_base,
    place_chart,
    target_dashboard,
)

logger = logging.getLogger(__name__)

ARTIFACT_DRAFT = "artifact.draft"
ARTIFACT_SAVED = "artifact.saved"
SAVE_SUGGESTED = "save.suggested"

#: The interactive-state slot of a message that holds its artifact log.
LOG_CONTENT_ID = "artifacts"
LOG_PROGRAM = hashlib.sha256(b"nova.studio.artifact-log.v1").hexdigest()
#: Later events one message may collect (edits and saves of all its charts).
MAX_LOG = 200
_CAS_ATTEMPTS = 3

Capability = Literal["can_save", "role_cannot_author"]


# ── Capability ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class AuthoringCapability:
    capability: Capability
    reason_code: str
    message: str

    def public(self) -> dict[str, Any]:
        return {"capability": self.capability, "reason_code": self.reason_code,
                "message": self.message}


def _role(user: dict[str, Any]) -> str:
    return str(user.get("active_role") or "your active role")


CAN_SAVE_MESSAGE = "You can save this chart to a Dataviz dashboard."


def cannot_read(user: dict[str, Any]) -> AuthoringCapability:
    return AuthoringCapability(
        "role_cannot_author", "ROLE_CANNOT_READ_SOURCE",
        f"Your active role {_role(user)} cannot read the data behind this chart, so it "
        "cannot be saved to a dashboard. Switch to a role that can read it and ask again.",
    )


def other_context() -> AuthoringCapability:
    return AuthoringCapability(
        "role_cannot_author", "ROLE_CHANGED",
        "This chart was made under another role or sign-in. Switch back to that role, or "
        "ask again under your current role, to save it.",
    )


async def authoring_capability(
    user: dict[str, Any], *, sql: str, database: str, dataset_id: UUID,
) -> AuthoringCapability:
    """Whether the caller's active role may save a chart over this statement.

    One zero-row inspection as the caller: Ranger decides whether the role can
    read the statement now. Infrastructure failures raise; they are not a
    refusal of the role.
    """
    from .contracts.authoring import SqlInspect, SqlInspection, SqlSelection
    from .dataset_service import native_dataset_service

    try:
        inspected = await native_dataset_service.inspect(user, SqlInspect(
            dataset_id=dataset_id, source=SqlSelection(database=database, sql=sql)))
    except NovaException as error:
        if error.status_code in (401, 403, 404):
            return cannot_read(user)
        raise
    if not isinstance(inspected, SqlInspection):
        return cannot_read(user)
    return AuthoringCapability("can_save", "OK", CAN_SAVE_MESSAGE)


# ── The artifact log ─────────────────────────────────────────────────


def _state_id(user: dict[str, Any], thread_id: str, message_id: str) -> str:
    body = json.dumps([str(user.get("username") or ""), thread_id, message_id,
                       LOG_CONTENT_ID, str(user.get("active_role") or "")],
                      separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def _entries(state: Any) -> list[dict[str, Any]]:
    if not isinstance(state, dict) or state.get("kind") != "artifact_log":
        return []
    return [item for item in state.get("entries") or []
            if isinstance(item, dict) and item.get("event") in (ARTIFACT_DRAFT, ARTIFACT_SAVED)
            and isinstance(item.get("data"), dict)]


async def _read_log(
    user: dict[str, Any], thread_id: str, message_id: str,
) -> tuple[int, list[dict[str, Any]]]:
    from app.core.metadata_scope import actor_for, metadata_transaction
    from app.modules.assistant.metadata_repository import AssistantMetadataRepository

    actor = actor_for(user)
    async with metadata_transaction(actor, read_only=True) as scope:
        row = await AssistantMetadataRepository(scope.connection, actor).get_ui_state(
            _state_id(user, thread_id, message_id))
    if row is None or row.get("program_sha256") != LOG_PROGRAM:
        return 0, []
    return int(row["revision"]), _entries(row.get("state"))


async def _write_log(
    user: dict[str, Any], thread_id: str, message_id: str, revision: int,
    entries: list[dict[str, Any]],
) -> None:
    from app.core.metadata_scope import actor_for, metadata_transaction
    from app.modules.assistant.metadata_repository import AssistantMetadataRepository

    actor = actor_for(user)
    async with metadata_transaction(actor) as scope:
        await AssistantMetadataRepository(scope.connection, actor).put_ui_state(
            _state_id(user, thread_id, message_id), thread_id=thread_id,
            message_id=message_id, content_id=LOG_CONTENT_ID, program_sha256=LOG_PROGRAM,
            state={"kind": "artifact_log", "version": 1, "entries": entries},
            expected_revision=revision,
        )


async def artifact_logs(user: dict[str, Any], thread_id: str) -> dict[str, list[dict[str, Any]]]:
    """Every message's artifact log on a thread, for the caller's active role."""
    from app.core.metadata_scope import actor_for, metadata_transaction
    from app.modules.assistant.metadata_repository import AssistantMetadataRepository

    actor = actor_for(user)
    async with metadata_transaction(actor, read_only=True) as scope:
        rows = await AssistantMetadataRepository(scope.connection, actor).ui_states_of(
            thread_id, LOG_CONTENT_ID)
    return {str(row["message_id"]): _entries(row.get("state")) for row in rows
            if row.get("program_sha256") == LOG_PROGRAM
            and row.get("state_id") == _state_id(user, thread_id, str(row["message_id"]))}


# ── Reading a draft's state from its journal and log ─────────────────


def journal_events(steps: Any) -> list[dict[str, Any]]:
    for step in steps or ():
        if isinstance(step, dict) and step.get("kind") == "event_journal":
            return [item for item in step.get("events") or [] if isinstance(item, dict)]
    return []


def _last_seq(events: list[dict[str, Any]]) -> int:
    values = [item["data"].get("seq") for item in events if isinstance(item.get("data"), dict)]
    return max((value for value in values if type(value) is int), default=0)


def _run_id(events: list[dict[str, Any]]) -> str | None:
    for item in events:
        data = item.get("data")
        if isinstance(data, dict) and isinstance(data.get("run_id"), str):
            return data["run_id"]
    return None


def numbered(journal: list[dict[str, Any]], log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The log as journal events, numbered after the turn's terminal event.

    The stored journal ends before ``run.finished``, which the live stream
    numbered right after it; later events start one past that.
    """
    base = _last_seq(journal) + 1
    run_id = _run_id(journal)
    return [
        {"event": item["event"], "data": {
            **item["data"], "seq": base + 1 + index, **({"run_id": run_id} if run_id else {}),
        }}
        for index, item in enumerate(log)
    ]


@dataclass
class DraftState:
    part_id: str
    suggestion_part_id: str | None
    revision: int
    draft: dict[str, Any]
    saved: dict[str, Any] | None


def draft_state(
    journal: list[dict[str, Any]], log: list[dict[str, Any]], ref: str,
) -> DraftState | None:
    state: DraftState | None = None
    suggestion: str | None = None
    for item in [*journal, *log]:
        data = item.get("data") or {}
        if item.get("event") == ARTIFACT_DRAFT:
            draft = data.get("draft")
            if isinstance(draft, dict) and draft.get("draft_ref") == ref:
                if state is None:
                    state = DraftState(str(data.get("part_id") or ""), None, 0, draft, None)
                state.revision = int(draft.get("revision") or state.revision + 1)
                state.draft = draft
        elif item.get("event") == SAVE_SUGGESTED and ref in (data.get("draft_refs") or []):
            suggestion = str(data.get("part_id") or "") or None
        elif (item.get("event") == ARTIFACT_SAVED and data.get("draft_ref") == ref
              and state is not None):
            state.saved = data.get("saved") if isinstance(data.get("saved"), dict) else None
    if state is not None:
        state.suggestion_part_id = suggestion
    return state


async def _draft_and_state(
    ref: str, user: dict[str, Any],
) -> tuple[ResolvedDraft, list[dict[str, Any]], int, list[dict[str, Any]], DraftState]:
    draft = await studio_drafts.resolve(ref, user)
    journal = journal_events(draft.steps)
    revision, log = await _read_log(user, draft.locator.thread_id, draft.message_id)
    state = draft_state(journal, log, ref)
    if state is None:
        raise DraftError("This chart is not in your conversations.")
    return draft, journal, revision, log, state


async def _append(
    user: dict[str, Any], draft: ResolvedDraft, revision: int, log: list[dict[str, Any]],
    entry: dict[str, Any], *, still_valid: Any,
) -> tuple[list[dict[str, Any]], int]:
    """Append one entry under the log's revision CAS; return the log and its index."""
    from app.core.metadata_db import MetadataConflict

    for _ in range(_CAS_ATTEMPTS):
        found = still_valid(log)
        if found is not None:
            return log, found
        if len(log) >= MAX_LOG:
            raise DraftError("This answer has too many chart changes. Ask again for a fresh "
                             "chart.", status_code=409)
        updated = [*log, entry]
        try:
            await _write_log(user, draft.locator.thread_id, draft.message_id, revision, updated)
            return updated, len(updated) - 1
        except MetadataConflict:
            revision, log = await _read_log(user, draft.locator.thread_id, draft.message_id)
    raise DraftError("This chart is being changed elsewhere. Reload the conversation.",
                     status_code=409)


def _served(journal: list[dict[str, Any]], log: list[dict[str, Any]], index: int) -> dict[str, Any]:
    return numbered(journal, log)[index]


# ── Edit before saving ───────────────────────────────────────────────


def _same_binding(chart: ChartDefinition, original: ChartDefinition) -> None:
    if (chart.chart_id != original.chart_id or chart.dataset_id != original.dataset_id
            or chart.dataset_revision != original.dataset_revision):
        raise DraftError("The edited chart must stay on its own data.", status_code=422)


async def _draft_dataset(user: dict[str, Any], draft: ResolvedDraft) -> Any:
    """The draft's statement as a dataset definition, inspected as the caller."""
    from .contracts.authoring import SqlInspect, SqlInspection, SqlSelection
    from .contracts.datasets import DatasetDefinition
    from .dataset_service import native_dataset_service

    try:
        inspected = await native_dataset_service.inspect(user, SqlInspect(
            dataset_id=draft.chart.dataset_id,
            source=SqlSelection(database=draft.database, sql=draft.sql)))
    except NovaException as error:
        if error.status_code in (401, 403, 404):
            raise DraftError(cannot_read(user).message, status_code=403) from None
        raise
    if not isinstance(inspected, SqlInspection):
        raise DraftError(cannot_read(user).message, status_code=403)
    return DatasetDefinition(
        dataset_id=draft.chart.dataset_id, title=draft.chart.title,
        source=inspected.source, fields=inspected.fields,
        schema_fingerprint=inspected.schema_fingerprint,
    )


async def revise(
    ref: str, user: dict[str, Any], *, chart: dict[str, Any], base_revision: int,
) -> dict[str, Any]:
    """Record an edited chart definition as the draft's next revision.

    The edit must keep the draft's own data binding and validate against the
    statement's fields as the caller may see them now. ``base_revision`` is the
    revision the editor started from; a concurrent edit is a conflict.
    """
    from .bindings import validate_chart_binding

    draft, journal, revision, log, state = await _draft_and_state(ref, user)
    if state.revision != base_revision:
        raise DraftError("This chart changed since you opened it. Reload the conversation.",
                         status_code=409)
    try:
        edited = ChartDefinition.model_validate(chart)
    except ValueError:
        raise DraftError("The edited chart is not valid.", status_code=422) from None
    _same_binding(edited, draft.chart)
    dataset = await _draft_dataset(user, draft)
    try:
        validate_chart_binding(edited, dataset)
    except ValueError as error:
        raise DraftError(f"The edited chart does not fit its data: {error}",
                         status_code=422) from None
    next_revision = state.revision + 1
    definition = edited.model_dump(mode="json")
    entry = {"event": ARTIFACT_DRAFT, "data": {"part_id": state.part_id, "draft": {
        **state.draft, "title": edited.title, "chart": definition, "revision": next_revision,
    }}}

    def conflict(current: list[dict[str, Any]]) -> int | None:
        latest = draft_state(journal, current, ref)
        if latest is None or latest.revision != base_revision:
            raise DraftError("This chart changed since you opened it. Reload the "
                             "conversation.", status_code=409)
        return None

    log, index = await _append(user, draft, revision, log, entry, still_valid=conflict)
    await _audit(user, draft, "REVISE", "SUCCESS")
    return {"revision": next_revision, "event": _served(journal, log, index)}


async def capability(ref: str, user: dict[str, Any]) -> dict[str, Any]:
    """The current capability of a draft for the caller, and its save receipt."""
    try:
        draft, _journal, _revision, _log, state = await _draft_and_state(ref, user)
    except DraftError as error:
        if error.status_code == 409:
            return {**other_context().public(), "saved": None}
        raise
    result = await authoring_capability(user, sql=draft.sql, database=draft.database,
                                        dataset_id=draft.chart.dataset_id)
    return {**result.public(), "saved": state.saved, "revision": state.revision}


async def state(ref: str, user: dict[str, Any]) -> dict[str, Any]:
    """What the artifact panel needs: the latest revision, its fields, capability and receipt.

    Fields are the statement's output columns as the caller may see them now;
    no rows are read.
    """
    try:
        draft, _journal, _revision, _log, current = await _draft_and_state(ref, user)
    except DraftError as error:
        if error.status_code == 409:
            return {**other_context().public(), "saved": None, "draft": None, "fields": [],
                    "revision": 0}
        raise
    try:
        dataset = await _draft_dataset(user, draft)
    except DraftError:
        return {**cannot_read(user).public(), "saved": current.saved, "draft": current.draft,
                "fields": [], "revision": current.revision}
    return {
        "capability": "can_save", "reason_code": "OK", "message": CAN_SAVE_MESSAGE,
        "saved": current.saved, "draft": current.draft, "revision": current.revision,
        "fields": [field.model_dump(mode="json") for field in dataset.fields],
    }


# ── Saving ───────────────────────────────────────────────────────────


def rebind(
    chart: ChartDefinition, field_map: dict[str, str], *, chart_id: UUID, dataset_id: UUID,
    revision: int,
) -> ChartDefinition:
    """The same chart over the saved dataset: identities move, nothing else changes.

    Field ids are derived from their dataset, so each draft field id maps to the
    saved dataset's field for the same source column. The visual, encoding,
    filters, style, sort and limits are copied as they are.
    """
    old = str(chart.dataset_id)

    def walk(value: Any) -> Any:
        if isinstance(value, dict):
            moved = {key: walk(item) for key, item in value.items()}
            if moved.get("dataset_id") == old:
                moved["dataset_id"] = str(dataset_id)
                if "dataset_revision" in moved:
                    moved["dataset_revision"] = revision
            return moved
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, str) and value in field_map:
            return field_map[value]
        return value

    payload = walk(chart.model_dump(mode="json"))
    payload["chart_id"] = str(chart_id)
    return ChartDefinition.model_validate(payload)


def _field_map(draft_fields: Any, saved_fields: Any) -> dict[str, str]:
    by_column = {field.source_column: field.field_id for field in saved_fields}
    mapping: dict[str, str] = {}
    for field in draft_fields:
        target = by_column.get(field.source_column)
        if target is None:
            raise PublishError("The data behind this chart changed. Ask again for a fresh "
                               "chart.", status=409, code="SCHEMA_CHANGED")
        mapping[field.field_id] = target
    return mapping


async def _dashboard_title(user: dict[str, Any], dashboard_id: UUID) -> str:
    from app.modules.agents.tools.native_dataviz import _draft

    try:
        _, dashboard = await _draft(user, dashboard_id)
    except Exception:  # noqa: BLE001 - the receipt still names the dashboard by id
        return ""
    return str(dashboard.title)


async def publish_draft(
    user: dict[str, Any], *, ref: str, idempotency_key: str, target: Target,
    title: str | None = None,
) -> dict[str, Any]:
    """Save a draft chart to a dashboard, exactly as the person last saw it.

    Runs as the caller: the draft must resolve under the current role and
    session, the role must read its statement now, and an existing dashboard
    must be editable by the caller. The dataset is created from the governed
    statement and the chart definition is copied as is (rebound to that
    dataset). Every step has an operation id derived from the idempotency key,
    so a retry returns the first outcome.
    """
    from app.modules.agents.tools.native_dataviz import Refusal, _dataset

    from .contracts.datasets import DatasetDefinition
    from .studio_publish import _attach_sql

    try:
        draft, journal, revision, log, state = await _draft_and_state(ref, user)
    except DraftError as error:
        if error.status_code == 409:
            await _audit(user, None, "PUBLISH", "DENIED")
            raise PublishError(other_context().message, status=403,
                               code="ROLE_CANNOT_AUTHOR") from error
        raise PublishError(error.message, status=error.status_code,
                           code="DRAFT_NOT_FOUND") from error
    chart = ChartDefinition.model_validate(state.draft.get("chart") or {})
    _same_binding(chart, draft.chart)
    base = operation_base(user, idempotency_key, f"{ref}#{state.revision}", target, title)
    chart_title = (title or "").strip()[:256] or chart.title
    if chart_title != chart.title:
        chart = chart.model_copy(update={"title": chart_title})
    try:
        try:
            draft_dataset = await _draft_dataset(user, draft)
        except DraftError as error:
            raise PublishError(error.message, status=403, code="ROLE_CANNOT_AUTHOR") from error
        dashboard_id = await target_dashboard(user, target, base, chart_title)
        workspace = await _documents_workspace(user, dashboard_id)
        dataset_id = uuid5(base, "dataset")
        await _attach_sql(user, dashboard_id, base, dataset_id=dataset_id, sql=draft.sql,
                          database=draft.database, title=chart_title)
        saved_revision, dataset = await _dataset(user, dataset_id, None)
        if not isinstance(dataset, DatasetDefinition):
            raise PublishError("The saved data could not be read back.", status=409,
                               code="CONFLICT")
        saved = rebind(chart, _field_map(draft_dataset.fields, dataset.fields),
                       chart_id=uuid5(base, "chart"), dataset_id=dataset_id,
                       revision=saved_revision)
        from .bindings import validate_chart_binding

        try:
            validate_chart_binding(saved, dataset)
        except ValueError as error:
            raise PublishError("This chart does not fit its saved data.",
                               code="NOT_REPRESENTABLE") from error
        receipt = await place_chart(user, dashboard_id, base, workspace, saved)
    except Refusal as exc:
        raise PublishError(str(exc)) from exc
    except NovaException as exc:
        refusal = _explain(exc)
        if refusal.code == "NOT_PERMITTED":
            refusal = PublishError(
                f"Your active role {_role(user)} cannot change that dashboard. Choose one you "
                "can edit, or create a new dashboard.", status=403, code="NOT_PERMITTED")
        await _audit(user, draft, "PUBLISH",
                     "DENIED" if refusal.status in (403, 404) else "FAILED")
        raise refusal from exc
    dashboard_title = (await _dashboard_title(user, dashboard_id)
                       or (target.title or chart_title))
    receipt_fields = {
        "operation_id": str(base),
        "dashboard_id": str(dashboard_id),
        "dashboard_title": dashboard_title,
        "workspace_id": workspace,
        "chart_id": str(saved.chart_id),
        "widget_id": str(uuid5(base, "widget")),
        "revision": int(receipt.get("new_revision") or 0) or None,
        "draft_revision": state.revision,
    }
    entry = {"event": ARTIFACT_SAVED, "data": {
        "part_id": state.part_id, "draft_ref": ref, "saved": receipt_fields,
        **({"suggestion_part_id": state.suggestion_part_id} if state.suggestion_part_id else {}),
    }}

    def recorded(current: list[dict[str, Any]]) -> int | None:
        for index, item in enumerate(current):
            data = item.get("data") or {}
            if (item.get("event") == ARTIFACT_SAVED
                    and (data.get("saved") or {}).get("operation_id") == str(base)):
                return index
        return None

    try:
        log, index = await _append(user, draft, revision, log, entry, still_valid=recorded)
        event: dict[str, Any] | None = _served(journal, log, index)
    except (DraftError, NovaException):
        # The chart is saved; only the conversation's note of it failed. A retry
        # with the same key records it without saving twice.
        logger.warning("Saved a chart but could not record it on the answer", exc_info=True)
        event = None
    await _audit(user, draft, "PUBLISH", "SUCCESS")
    return {"status": "SUCCEEDED", **receipt_fields, "event": event}


async def _documents_workspace(user: dict[str, Any], dashboard_id: UUID) -> str:
    from .studio_publish import _documents

    return str(await _documents().workspace_of(user, dashboard_id))


async def _audit(user: dict[str, Any], draft: ResolvedDraft | None, action: str,
                 status: str) -> None:
    from app.common.audit import write_audit_log

    try:
        await write_audit_log(
            event_type="viz", action=f"DRAFT_{action}", object_type="studio_draft_chart",
            object_name=(draft.locator.content_id if draft else "invalid_reference")[:128],
            user_name=str(user.get("username") or ""),
            active_role=user.get("active_role"), session_id=user.get("session_id"),
            security_context_version=user.get("security_context_version"),
            status=status, decision="ALLOW" if status == "SUCCESS" else "DENY",
        )
    except Exception:  # noqa: BLE001 - an audit outage must not leak through the response
        logger.warning("Draft artifact audit write failed", exc_info=True)


# ── The turn: artifact events and the save suggestion ────────────────


def statement_for(steps: list[Any], source: dict[str, Any]) -> str | None:
    """The governed statement a draft's recorded source names, from this turn's steps."""
    producer_id = source.get("tool_call_id")
    if isinstance(producer_id, str):
        producer = next((
            item for item in steps
            if isinstance(item, dict) and item.get("kind") == "tool"
            and item.get("tool_call_id") == producer_id and item.get("status") in ("done", None)
        ), None)
        return studio_drafts._statement(producer)
    recorded = source.get("statement")
    return recorded.strip() if isinstance(recorded, str) and recorded.strip() else None


def draft_payload(step: dict[str, Any]) -> dict[str, Any] | None:
    """The ``artifact.draft`` payload for a placed ``DatavizChart`` step: no rows, no SQL."""
    props = step.get("props") or {}
    ref = props.get("draft_ref")
    chart = props.get("chart")
    if step.get("component") != "DatavizChart" or not isinstance(ref, str) \
            or not isinstance(chart, dict):
        return None
    return {
        "draft_ref": ref,
        "kind": "dataviz_chart",
        "title": str(props.get("title") or chart.get("title") or "Chart"),
        "chart": chart,
        "revision": DRAFT_REVISION,
        "content_id": step.get("content_id"),
    }


SUGGESTION_ONE = "Save this chart to a Dataviz dashboard to keep it."
SUGGESTION_MANY = "Save these {count} charts to a Dataviz dashboard to keep them."


# ── Replay ───────────────────────────────────────────────────────────


def with_log(steps: list[Any], log: list[dict[str, Any]]) -> list[Any]:
    """A message's steps with its artifact log appended to its event journal."""
    if not log:
        return steps
    result: list[Any] = []
    for step in steps:
        if isinstance(step, dict) and step.get("kind") == "event_journal":
            events = [item for item in step.get("events") or [] if isinstance(item, dict)]
            step = {**step, "events": [*events, *numbered(events, log)]}
        result.append(step)
    return result


async def overlay(thread_id: str, user: dict[str, Any], views: list[Any]) -> list[Any]:
    """Thread message views with each answer's later artifact edits and saves.

    Best-effort: when the log cannot be read the answer replays as the turn
    left it, and the panel re-reads a draft's state before any edit or save.
    """
    if not any(any(isinstance(step, dict) and step.get("kind") == "artifact_draft"
                   for step in view.steps or []) for view in views):
        return views
    try:
        logs = await artifact_logs(user, thread_id)
    except Exception:  # noqa: BLE001 - replay degrades to the turn's own journal
        logger.warning("Artifact log unavailable for a thread read", exc_info=True)
        return views
    for view in views:
        log = logs.get(view.message_id)
        if log:
            view.steps = with_log(list(view.steps or []), log)
    return views
