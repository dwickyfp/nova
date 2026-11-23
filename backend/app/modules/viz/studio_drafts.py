"""Draft charts in a Studio answer: a native chart bound to a governed query.

A Studio data answer shows its charts as native Dataviz ``ChartDefinition``\\ s
(the ``DatavizChart`` registry component) before anything is saved. Each one
carries a ``draft_ref``: an HMAC-signed, caller-bound pointer
``{thread, content}`` to the component Nova recorded in the caller's own
assistant message. The reference holds no SQL, rows or credentials.

Reading a draft's data (``POST /agents/studio/dataviz/drafts/{ref}/query``):

1. the signature must match the caller's principal;
2. the thread must be the caller's, and exactly one of the caller's assistant
   messages on it must hold that component;
3. the message must have been produced under the caller's current principal
   and active role (and, within one sign-in, security-context version);
4. the statement is read back from Nova's own record, never from the client:
   the producing tool step named by the component (``tool_call_id``), or the
   statement the component recorded when its producer was in an earlier turn,
   and its fingerprint must match the one recorded with the chart;
5. the aggregate request is planned over that statement by the native dataset
   planner and executed through QueryService as the caller under the active
   role, so Ranger row filters and column masks apply. Stored rows are never
   returned.

Every query writes one audit record, allowed or refused. Saving a draft to a
dashboard is a separate, explicit action.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from app.core.config import settings
from app.core.exceptions import NovaException
from app.core.metadata_scope import actor_for, metadata_transaction
from app.modules.assistant.metadata_repository import AssistantMetadataRepository

from .contracts.charts import ChartDefinition
from .contracts.queries import DatasetQueryRequest, DatasetQueryResult
from .studio_results import fingerprint, normal_sql

logger = logging.getLogger(__name__)

_PREFIX = "dr1"
_MAX_REF = 1024
_ID = re.compile(r"^[A-Za-z0-9_.:\-]{1,128}$")
_PRODUCERS = frozenset({"semantic_query", "query_execute"})
#: Namespace of draft dataset ids: one per thread and producing chart call.
DRAFT_NAMESPACE = uuid5(NAMESPACE_URL, "https://nova/studio/draft-dataset")
#: A draft dataset has one revision: the statement it was drawn from.
DRAFT_REVISION = 1


class DraftError(NovaException):
    """A refusal with a plain message; never names SQL or another user's data."""

    def __init__(self, message: str, *, status_code: int = 404) -> None:
        super().__init__(message, status_code=status_code)


def draft_dataset_id(thread_id: str, tool_call_id: str) -> UUID:
    return uuid5(DRAFT_NAMESPACE, f"{thread_id}\n{tool_call_id}")


def _key() -> bytes:
    if not settings.SECRET_KEY:
        raise DraftError("Draft charts need the deployment signing key.", status_code=503)
    return hmac.new(settings.SECRET_KEY.encode(), b"nova.studio.draft.v1", hashlib.sha256).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass(frozen=True)
class DraftLocator:
    thread_id: str
    content_id: str
    #: The chart's position inside an interactive answer; None for a chart block.
    node: int | None = None


def sign(locator: DraftLocator, username: str) -> str:
    value: dict[str, Any] = {"t": locator.thread_id, "c": locator.content_id}
    if locator.node is not None:
        value["n"] = locator.node
    payload = _b64(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
    mac = hmac.new(_key(), f"{username}\n{payload}".encode(), hashlib.sha256).digest()
    return f"{_PREFIX}.{payload}.{_b64(mac[:18])}"


def verify(ref: str, username: str) -> DraftLocator:
    refused = DraftError("This chart is not available to you.")
    if not isinstance(ref, str) or len(ref) > _MAX_REF or ref.count(".") != 2 or not username:
        raise refused
    prefix, payload, mac = ref.split(".")
    expected = hmac.new(_key(), f"{username}\n{payload}".encode(), hashlib.sha256).digest()
    try:
        given = _unb64(mac)
        value = json.loads(_unb64(payload))
    except (ValueError, TypeError):
        raise refused from None
    if prefix != _PREFIX or not hmac.compare_digest(given, expected[:18]):
        raise refused
    thread, content = value.get("t") if isinstance(value, dict) else None, (
        value.get("c") if isinstance(value, dict) else None)
    if not all(isinstance(item, str) and _ID.match(item) for item in (thread, content)):
        raise refused
    node = value.get("n")
    if node is not None and (isinstance(node, bool) or not isinstance(node, int)
                             or not 0 <= node < 64):
        raise refused
    return DraftLocator(str(thread), str(content), node)


def source_record(
    *, evidence_id: str | None, sql: str, database: str, producer_tool_call_id: str | None,
) -> dict[str, Any]:
    """What the component step records about its governed source.

    The statement itself is recorded only when no tool step of the same turn
    produced it (a result kept from an earlier turn); otherwise the producing
    step is the statement's only record, as for Add to Dataviz.
    """
    record: dict[str, Any] = {
        "statement_fingerprint": fingerprint(normal_sql(sql)),
        "database": database,
    }
    if evidence_id:
        record["evidence_id"] = evidence_id
    if producer_tool_call_id:
        record["tool_call_id"] = producer_tool_call_id
    else:
        record["statement"] = sql
    return record


@dataclass
class ResolvedDraft:
    locator: DraftLocator
    chart: ChartDefinition
    sql: str
    database: str
    #: The assistant message holding the draft, and its recorded steps.
    message_id: str = ""
    steps: tuple[Any, ...] = ()


def _statement(step: dict[str, Any] | None) -> str | None:
    if not step or step.get("name") not in _PRODUCERS:
        return None
    sql = str(step.get("preview") or "").strip()
    return sql if re.match(r"^\s*(SELECT|WITH)\b", sql, re.IGNORECASE) else None


async def resolve(ref: str, user: dict[str, Any]) -> ResolvedDraft:
    """The chart and statement behind a draft, read from the caller's own record."""
    from app.modules.assistant.security import (
        observation_context,
        same_ui_context,
        session_security,
    )

    locator = verify(ref, str(user.get("username") or ""))
    actor = actor_for(user)
    async with metadata_transaction(actor, read_only=True) as scope:
        repository = AssistantMetadataRepository(scope.connection, actor)
        thread = await repository.get_thread(locator.thread_id)
        rows = (
            await repository.message_with_content(locator.thread_id, locator.content_id)
            if thread else []
        )
    if not thread or thread.get("user_name") != actor.principal or len(rows) != 1:
        raise DraftError("This chart is not in your conversations.")
    row = rows[0]
    steps = row["steps"]
    steps = json.loads(steps) if isinstance(steps, str) else (steps or [])
    stamp = row["security_context"]
    stamp = json.loads(stamp) if isinstance(stamp, str) else stamp
    if not same_ui_context(stamp, observation_context(session_security(user))):
        raise DraftError(
            "This chart was made under another role or session. Ask again under your "
            "current role to see it.", status_code=409,
        )
    step = _chart_record(steps, locator)
    raw_source = step.get("source")
    source: dict[str, Any] = raw_source if isinstance(raw_source, dict) else {}
    producer_id = source.get("tool_call_id")
    sql: str | None
    if isinstance(producer_id, str):
        producer = next((
            item for item in steps
            if isinstance(item, dict) and item.get("kind") == "tool"
            and item.get("tool_call_id") == producer_id and item.get("status") in ("done", None)
        ), None)
        sql = _statement(producer)
    else:
        recorded = source.get("statement")
        sql = recorded.strip() if isinstance(recorded, str) and re.match(
            r"^\s*(SELECT|WITH)\b", recorded, re.IGNORECASE) else None
    database = source.get("database")
    if (
        not sql or not isinstance(database, str) or not database
        or fingerprint(normal_sql(sql)) != source.get("statement_fingerprint")
    ):
        raise DraftError("The query behind this chart was not recorded.", status_code=422)
    try:
        chart = ChartDefinition.model_validate(step.get("chart"))
    except ValueError:
        raise DraftError("This chart's stored definition cannot be read.",
                         status_code=422) from None
    return ResolvedDraft(locator=locator, chart=chart, sql=normal_sql(sql), database=database,
                         message_id=str(row.get("message_id") or ""), steps=tuple(steps))


def _chart_record(steps: list[Any], locator: DraftLocator) -> dict[str, Any]:
    """The recorded chart: ``{"chart", "source"}`` from a component or program."""
    missing = DraftError("This chart is not in your conversations.")
    blocks = [step for step in steps
              if isinstance(step, dict) and step.get("content_id") == locator.content_id]
    if locator.node is None:
        found = [step for step in blocks if step.get("kind") == "component"
                 and step.get("component") == "DatavizChart"]
        if len(found) != 1:
            raise missing
        return {"chart": (found[0].get("props") or {}).get("chart"),
                "source": found[0].get("source")}
    programs = [step for step in blocks if step.get("kind") == "ui"]
    charts = programs[0].get("charts") if len(programs) == 1 else None
    if not isinstance(charts, list) or locator.node >= len(charts) \
            or not isinstance(charts[locator.node], dict):
        raise missing
    return {"chart": charts[locator.node].get("chart"),
            "source": charts[locator.node].get("source")}


async def _audit(user: dict[str, Any], locator: DraftLocator | None, status: str) -> None:
    from app.common.audit import write_audit_log

    try:
        await write_audit_log(
            event_type="viz", action="DRAFT_QUERY", object_type="studio_draft_chart",
            object_name=(
                f"{locator.content_id}#{locator.node}" if locator and locator.node is not None
                else locator.content_id if locator else "invalid_reference"
            )[:128],
            user_name=str(user.get("username") or ""),
            active_role=user.get("active_role"), session_id=user.get("session_id"),
            security_context_version=user.get("security_context_version"),
            status=status, decision="ALLOW" if status == "SUCCESS" else "DENY",
        )
    except Exception:  # noqa: BLE001 - an audit outage must not leak through the response
        logger.warning("Draft query audit write failed", exc_info=True)


async def query(
    ref: str, user: dict[str, Any], request: DatasetQueryRequest,
) -> DatasetQueryResult:
    """One aggregate read of a draft chart, as the caller under the active role."""
    from .dataset_service import native_dataset_service

    locator: DraftLocator | None = None
    try:
        draft = await resolve(ref, user)
        locator = draft.locator
        if request.revision != DRAFT_REVISION:
            raise DraftError("This chart changed; reload the conversation.", status_code=409)
        result = await native_dataset_service.query_statement(
            user, identity=draft.chart.dataset_id, database=draft.database,
            sql=draft.sql, request=request,
        )
    except NovaException as error:
        await _audit(user, locator, "DENIED" if error.status_code in (401, 403, 404, 409)
                     else "FAILED")
        raise
    await _audit(user, locator, "SUCCESS")
    return result
