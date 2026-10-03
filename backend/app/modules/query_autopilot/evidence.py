from __future__ import annotations

import asyncio
import re
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.common.identifiers import check_identifier
from app.common.sql_guard import redact_sql_credentials
from app.core.config import settings
from app.modules.query_autopilot.models import Availability, Evidence, Policy, Scope, utcnow
from app.modules.query_autopilot.payloads import PayloadStore
from app.modules.query_autopilot.runtime import (
    AuthorizationUnavailable,
    AuthorizedSQL,
    EvidenceUnsupported,
)
from app.sql_frontend.autopilot import quote_path

_OPERATOR = re.compile(r"^\s*(?:[|+\- ]*)(\d+)\s*:\s*([A-Za-z][A-Za-z0-9_ ]*)$")
_NUMBER = re.compile(r"\b(cardinality|avgRowSize|cpu|memory|cost)\s*[:=]\s*([\d.eE+-]+)", re.I)


def structured_plan(text: str) -> dict:
    operators: list[dict[str, Any]] = []
    for line in text.splitlines():
        match = _OPERATOR.match(line)
        if match:
            operators.append(
                {
                    "ordinal": len(operators),
                    "node_id": int(match.group(1)),
                    "operator": match.group(2).strip(),
                    "estimates": {},
                    "attributes": {},
                }
            )
        elif operators:
            attribute = re.search(r"\b(join op|TABLE|rollup):\s*([A-Za-z0-9_ .()`-]+)", line)
            if attribute:
                operators[-1]["attributes"][attribute.group(1).lower()] = attribute.group(2).strip()
            for metric in _NUMBER.finditer(line):
                with suppress(ValueError):
                    operators[-1]["estimates"][metric.group(1).lower()] = float(metric.group(2))
    from app.modules.query_autopilot.models import digest

    return {
        "operators": operators,
        "hash": digest([{k: v for k, v in op.items() if k != "estimates"} for op in operators]),
    }


def plan_diff(before: dict, after: dict) -> list[dict]:
    changes = []
    old, new = before.get("operators", []), after.get("operators", [])
    for index in range(max(len(old), len(new))):
        a = old[index] if index < len(old) else None
        b = new[index] if index < len(new) else None
        if a != b:
            changes.append({"ordinal": index, "before": a, "after": b})
    return changes


class EvidenceCollector:
    def __init__(
        self, sql: AuthorizedSQL, repository, payloads: PayloadStore | None = None
    ) -> None:
        self.sql, self.repository = sql, repository
        self.payloads = payloads or PayloadStore()

    async def capture(
        self,
        *,
        family_id: str,
        scope: Scope,
        kind: str,
        statement: str,
        connection,
        query_ids: tuple[str, ...] = (),
        observed_at: datetime | None = None,
        parameter_digest: str | None = None,
    ) -> Evidence:
        identifier = str(uuid4())
        now = utcnow()
        policy = Policy.model_validate(await self.repository.get("policies", "default") or {})
        record = Evidence(
            id=identifier,
            family_id=family_id,
            cohort_id=scope.cohort_id,
            kind=kind,
            availability=Availability.UNAVAILABLE,
            source="starrocks",
            collected_at=now,
            expires_at=now + timedelta(hours=policy.payload_hours),
            query_ids=query_ids,
            reason="capture_pending",
            payload_ref=self.payloads.reference(identifier),
        )
        await self.repository.put(
            "evidence",
            identifier,
            record.model_dump(mode="json"),
            family_id=family_id,
            cohort_id=scope.cohort_id,
            expires_at=record.expires_at,
        )
        try:
            feature = {
                "profile": "query_profiles",
                "analyzed_profile": "query_profiles",
                "logs": "be_logs",
            }.get(kind)
            local_paths = settings.QUERY_AUTOPILOT_LOCAL_LOG_FIXTURES.get(scope.cohort_id)
            local_logs = kind == "logs" and bool(local_paths)
            if feature and not local_logs and not getattr(await self.sql.capabilities(), feature):
                raise EvidenceUnsupported("engine_capability_unavailable")
            if local_logs:
                from app.modules.query.repository import QueryResult

                # The operator configuration binds these fixture files to one
                # cohort. Verify its live engine role before reading any bytes.
                await self.sql.execute(
                    "SELECT 1", scope, connection=connection, category="diagnostic"
                )
                lines = await LocalLogAdapter(tuple(local_paths or ())).read(query_ids[0])
                result = QueryResult(
                    columns=["LOG"], rows=[[line] for line in lines], row_count=len(lines)
                )
                record = record.model_copy(update={"source": "configured_local_fixture"})
            else:
                result = await self.sql.execute(
                    statement, scope, connection=connection, category="diagnostic", max_rows=2000
                )
            if result.truncated:
                raise ValueError("evidence_budget_exceeded")
            text = redact_sql_credentials(
                "\n".join(" | ".join(str(value) for value in row) for row in result.rows)
            )
            if kind not in {"statistics", "logs", "resource"} and (
                not result.rows or not text.strip() or text.strip().lower() in {"none", "null"}
            ):
                raise ValueError("evidence_not_retained_by_engine")
            reference = await self.payloads.put(identifier, text)
            if kind == "plan":
                summary = structured_plan(text)
            elif kind == "analyzed_profile":
                from app.modules.query_autopilot.profile import analyzed_profile_summary

                summary = analyzed_profile_summary(text, query_id=query_ids[0])
            elif kind == "profile":
                from app.modules.query_autopilot.profile import profile_summary

                summary = profile_summary(text)
            elif kind == "statistics":
                from app.modules.query_autopilot.statistics_evidence import statistics_summary

                summary = statistics_summary(result.columns, result.rows)
            elif kind == "resource":
                from app.modules.query_autopilot.statistics_evidence import resource_summary

                summary = resource_summary(result.columns, result.rows)
            elif kind == "logs":
                from app.modules.query_autopilot.log_evidence import log_summary

                summary = log_summary(text, query_ids[0])
            else:
                summary = {"row_count": result.row_count, "columns": result.columns}
            if parameter_digest:
                summary["parameter_digest"] = parameter_digest
            if observed_at:
                summary["observed_at"] = observed_at.isoformat()
            record = record.model_copy(
                update={
                    "availability": Availability.AVAILABLE,
                    "payload_ref": reference,
                    "summary": summary,
                    "reason": None,
                }
            )
        except EvidenceUnsupported:
            record = record.model_copy(
                update={
                    "availability": Availability.UNSUPPORTED,
                    "reason": "engine_capability_unavailable",
                }
            )
        except AuthorizationUnavailable:
            record = record.model_copy(
                update={
                    "availability": Availability.UNAUTHORIZED,
                    "reason": "caller_authorization_unavailable",
                }
            )
        except Exception:
            record = record.model_copy(update={"reason": "engine_or_payload_unavailable"})
        await self.repository.put(
            "evidence",
            identifier,
            record.model_dump(mode="json"),
            family_id=family_id,
            cohort_id=scope.cohort_id,
            expires_at=record.expires_at,
        )
        return record

    async def profile(
        self, family_id: str, scope: Scope, query_id: str, connection, *, observed_at=None
    ) -> Evidence:
        identifier = str(UUID(query_id))
        return await self.capture(
            family_id=family_id,
            scope=scope,
            kind="profile",
            statement=f"SELECT get_query_profile('{identifier}')",
            connection=connection,
            query_ids=(identifier,),
            observed_at=observed_at,
        )

    async def analyze_profile(
        self, family_id: str, scope: Scope, query_id: str, connection, *, observed_at=None
    ) -> Evidence:
        identifier = str(UUID(query_id))
        return await self.capture(
            family_id=family_id,
            scope=scope,
            kind="analyzed_profile",
            statement=f"ANALYZE PROFILE FROM '{identifier}'",
            connection=connection,
            query_ids=(identifier,),
            observed_at=observed_at,
        )

    async def statistics(
        self, family_id: str, scope: Scope, table: str, connection
    ) -> list[Evidence]:
        path = quote_path(table)
        database = check_identifier(
            path.split(".")[-2].strip("`") if "." in path else scope.database, field="database"
        )
        return [
            await self.capture(
                family_id=family_id,
                scope=scope,
                kind="statistics",
                statement=(
                    f"SHOW STATS META WHERE `Table` = '{path.split('.')[-1].strip('`')}'"
                    f" AND `Database` = '{database}'"
                ),
                connection=connection,
            ),
            await self.capture(
                family_id=family_id,
                scope=scope,
                kind="table",
                statement=f"SHOW PARTITIONS FROM {path}",
                connection=connection,
            ),
        ]

    async def resource(self, family_id: str, scope: Scope, group: str, connection) -> Evidence:
        from app.common.identifiers import check_identifier

        group = check_identifier(group, field="resource group")
        return await self.capture(
            family_id=family_id,
            scope=scope,
            kind="resource",
            connection=connection,
            statement=(
                "SELECT BE_ID,NAME,LABELS,VALUE FROM information_schema.be_metrics "
                f"WHERE LABELS = 'name={group}' AND NAME LIKE 'resource_group_%' LIMIT 1000"
            ),
        )

    async def logs(self, family_id: str, scope: Scope, query_id: str, connection) -> Evidence:
        identifier = str(UUID(query_id))
        return await self.capture(
            family_id=family_id,
            scope=scope,
            kind="logs",
            statement=(
                f"SELECT * FROM information_schema.be_logs WHERE LOG LIKE "
                f"'%{identifier}%' LIMIT 200"
            ),
            connection=connection,
            query_ids=(identifier,),
        )


class LocalLogAdapter:
    def __init__(self, configured_paths: tuple[Path, ...]) -> None:
        self.paths = configured_paths

    async def read(self, query_id: str) -> list[str]:
        identifier = str(UUID(query_id))

        def scan() -> list[str]:
            lines = []
            for path in self.paths:
                with path.open("rb") as stream:
                    stream.seek(max(0, path.stat().st_size - 1048576))
                    text = stream.read(1048576).decode("utf-8", errors="replace")
                for line in text.splitlines():
                    if identifier in line:
                        lines.append(redact_sql_credentials(line)[:2048])
                        if len(lines) >= 200:
                            return lines
            return lines

        return await asyncio.to_thread(scan)


def plan_uses_object(result, name: str) -> bool:
    if result.truncated:
        return False
    plan = structured_plan("\n".join(str(value) for row in result.rows for value in row))
    return any(
        str(value).split(".")[-1].strip("` ") == name
        for operator in plan["operators"]
        for key, value in operator.get("attributes", {}).items()
        if key in {"table", "rollup"}
    )
