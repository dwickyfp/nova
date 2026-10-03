"""Read engine postconditions and compensate only objects created by this action."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime

from app.common.identifiers import check_identifier
from app.modules.query_autopilot.models import ActionKind, Candidate, Scope, digest
from app.sql_frontend.autopilot import quote_path


def object_name(candidate: Candidate) -> str:
    name = check_identifier(
        candidate.parameters.get("name") or f"nova_ap_{candidate.id.replace('-', '')[:24]}",
        field="autopilot object",
    )
    if not name.startswith("nova_ap_"):
        raise ValueError("autopilot_owned_object_required")
    return name


@dataclass(frozen=True)
class ObjectState:
    exists: bool
    identity: str | None = None
    definition_digest: str | None = None
    ready: bool = False

    @property
    def binding(self) -> str:
        return digest([self.exists, self.identity, self.definition_digest])


def resource_group_state(result, name: str) -> ObjectState:
    if result.truncated:
        raise ValueError("object_inventory_truncated")
    rows = [dict(zip(result.columns, row, strict=True)) for row in result.rows]
    matches = [row for row in rows if row.get("name") == name]
    if not matches:
        return ObjectState(False)
    if len(matches) != 1 or not str(matches[0].get("id", "")).isdecimal():
        raise ValueError("resource_group_identity_ambiguous")
    row = matches[0]
    return ObjectState(True, str(row["id"]), digest(row), True)


async def inspect_object(sql, candidate: Candidate, scope: Scope, connection) -> ObjectState:
    if candidate.kind == ActionKind.RESOURCE_GROUP:
        name = sandbox_resource_name(candidate)
        result = await sql.execute(
            "SHOW RESOURCE GROUPS ALL", scope, connection=connection, category="diagnostic"
        )
        return resource_group_state(result, name)
    if candidate.kind == ActionKind.PLAN_BASELINE:
        identity = (candidate.owned_object or "").removeprefix("baseline:")
        if not identity.isdecimal():
            raise ValueError("baseline_ownership_identity_required")
        result = await sql.execute(
            f"SHOW BASELINE WHERE Id = {identity}",
            scope,
            connection=connection,
            category="diagnostic",
        )
        return baseline_state(result, identity)
    name = object_name(candidate)
    database = check_identifier(scope.database, field="database")
    if candidate.kind in {ActionKind.MATERIALIZED_VIEW, ActionKind.REFRESH_POLICY}:
        statement = f"SHOW MATERIALIZED VIEWS FROM `{database}`"
        result = await sql.execute(statement, scope, connection=connection, category="diagnostic")
        if result.truncated:
            raise ValueError("object_inventory_truncated")
        for row in result.rows:
            data = dict(zip(result.columns, row, strict=True))
            if data.get("name") == name:
                return ObjectState(
                    True,
                    str(data["id"]),
                    digest(data["text"]),
                    materialized_view_ready(data),
                )
        return ObjectState(False)
    if candidate.kind == ActionKind.ADD_INDEX:
        table = quote_path(candidate.targets[0])
        result = await sql.execute(
            f"SHOW INDEX FROM {table}", scope, connection=connection, category="diagnostic"
        )
        if result.truncated:
            raise ValueError("object_inventory_truncated")
        for row in result.rows:
            data = dict(zip(result.columns, row, strict=True))
            if data.get("Key_name") == name:
                jobs = await sql.execute(
                    f"SHOW ALTER TABLE COLUMN FROM `{database}`",
                    scope,
                    connection=connection,
                    category="diagnostic",
                )
                if jobs.truncated:
                    raise ValueError("object_inventory_truncated")
                unfinished = [dict(zip(jobs.columns, row, strict=True)) for row in jobs.rows]
                ready = not any(
                    item.get("TableName") == candidate.targets[0].split(".")[-1].strip("`")
                    and item.get("State") not in {"FINISHED", "CANCELLED"}
                    for item in unfinished
                )
                return ObjectState(True, name, digest(data), ready)
        return ObjectState(False)
    raise ValueError("object_reconciliation_unsupported")


def materialized_view_ready(data: dict) -> bool:
    if str(data.get("is_active")).lower() != "true":
        return False
    state = data.get("last_refresh_state")
    if state == "SUCCESS":
        return True
    if (
        state != "SKIPPED"
        or data.get("query_rewrite_status") != "VALID"
        or str(data.get("last_refresh_error_code")) != "0"
        or data.get("last_refresh_error_message") not in (None, "", "NULL")
    ):
        return False
    # The pinned FE can finish a redundant refresh as SKIPPED after a successful
    # refresh. Require its explicit freshness confirmation and base versions.
    try:
        versions = data.get("base_table_refresh_version_times")
        if isinstance(versions, str):
            versions = json.loads(versions)
        if not isinstance(versions, dict) or not versions:
            return False
        confirmed = datetime.fromisoformat(str(data["last_freshness_confirmed_at"]))
        refreshed = datetime.fromisoformat(str(data["last_refresh_time"]))
        return confirmed >= refreshed and all(
            confirmed >= datetime.fromisoformat(str(value)) for value in versions.values()
        )
    except (KeyError, TypeError, ValueError):
        return False


async def wait_ready(
    sql, candidate: Candidate, scope: Scope, connection, *, timeout: float = 120
) -> ObjectState:
    try:
        async with asyncio.timeout(timeout):
            while True:
                state = await inspect_object(sql, candidate, scope, connection)
                if state.exists and state.ready:
                    return state
                await asyncio.sleep(0.5)
    except TimeoutError:
        raise ValueError("engine_object_not_ready_outcome_uncertain") from None


def compensation_sql(candidate: Candidate) -> str:
    if candidate.kind == ActionKind.RESOURCE_GROUP:
        return f"DROP RESOURCE GROUP `{sandbox_resource_name(candidate)}`"
    if candidate.kind == ActionKind.PLAN_BASELINE:
        identity = (candidate.owned_object or "").removeprefix("baseline:")
        if not identity.isdecimal():
            raise ValueError("baseline_ownership_identity_required")
        return f"DROP BASELINE {identity}"
    name = object_name(candidate)
    if candidate.kind == ActionKind.MATERIALIZED_VIEW:
        return f"DROP MATERIALIZED VIEW `{name}`"
    if candidate.kind == ActionKind.ADD_INDEX:
        from app.modules.indexes.router import DropIndexRequest, build_drop_index_sql

        parts = candidate.targets[0].split(".")
        return build_drop_index_sql(
            DropIndexRequest(
                database=parts[-2] if len(parts) > 1 else candidate.scope.database,
                table=parts[-1],
                index_name=name,
            )
        )
    raise ValueError("no_proven_reversible_operation")


def sandbox_resource_name(candidate: Candidate) -> str:
    prefix = "sandbox-resource-group:"
    if not (candidate.owned_object or "").startswith(prefix):
        raise ValueError("sandbox_resource_group_ownership_required")
    owned_object = candidate.owned_object or ""
    name = check_identifier(owned_object[len(prefix):], field="resource group")
    if not name.startswith("nova_ap_") or candidate.parameters.get("resource_group") != name:
        raise ValueError("sandbox_resource_group_ownership_required")
    return name


def baseline_state(result, identity: str | None = None) -> ObjectState:
    if result.truncated:
        raise ValueError("baseline_inventory_truncated")
    rows = [dict(zip(result.columns, row, strict=True)) for row in result.rows]
    if identity is not None:
        rows = [row for row in rows if str(row.get("Id")) == identity]
    if not rows:
        return ObjectState(False)
    if len(rows) != 1:
        raise ValueError("baseline_identity_ambiguous")
    row = rows[0]
    identifier = str(row.get("Id", ""))
    required = {"Id", "global", "enable", "bindSQL", "planSQL", "source", "updateTime"}
    if not identifier.isdecimal() or not required.issubset(row):
        raise ValueError("baseline_inventory_unsupported")
    return ObjectState(
        True,
        identifier,
        digest({key: row[key] for key in sorted(required)}),
        row["global"] == "Y" and row["enable"] == "Y" and row["source"] == "USER",
    )


async def baseline_for_sample(sql, sample: str, scope: Scope, connection) -> ObjectState:
    from app.sql_frontend.fingerprint import fingerprint

    if not fingerprint(sample).replay_eligible:
        raise ValueError("baseline_requires_deterministic_read_only_sample")
    result = await sql.execute(
        "SHOW BASELINE ON " + sample, scope, connection=connection, category="diagnostic"
    )
    return baseline_state(result)


async def registered_snapshot_state(sql, enrollment, scope: Scope, connection) -> str | None:
    states = []
    for table in sorted(set(enrollment.table_mapping.values())):
        value = await sql.execute(
            f"SHOW PARTITIONS FROM {quote_path(table)}", scope,
            connection=connection, category="diagnostic",
        )
        if value.truncated or not value.rows:
            return None
        try:
            indexes = [value.columns.index(name) for name in ("PartitionId", "VisibleVersion")]
            pairs = []
            for row in value.rows:
                pair = [row[index] for index in indexes]
                if any(type(number) is bool or not str(number).isdecimal()
                       or int(number) < 1 for number in pair):
                    return None
                pairs.append([int(number) for number in pair])
        except (ValueError, IndexError, TypeError):
            return None
        if len({pair[0] for pair in pairs}) != len(pairs):
            return None
        states.append([table, sorted(pairs)])
    return digest([enrollment.snapshot_id, states]) if states else None
