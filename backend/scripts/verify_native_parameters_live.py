"""Read-only parameter acceptance against the existing governed QA sales fixture.

NOVA_QA_OWNER_PASSWORD supplies the principal's credential. This does not create,
alter or reset fixture data or grants. Nova records the normal query audit events.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import traceback
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from app.core.database import db
from app.core.exceptions import StarRocksError
from app.core.metadata_process import close_process_metadata, open_process_metadata
from app.core.metadata_scope import acting_as_sync
from app.core.security import encrypt_password
from app.modules.access_control.security_context import require_security_context
from app.modules.query.repository import RowSink
from app.modules.query.service import QueryService
from app.sql_frontend.parameters import BoundParameters, ParameterSpec


async def run(principal: str, city: str, output: Path) -> dict:
    report: dict = {"result": "RUNNING", "principal": principal, "steps": []}
    output.write_text(json.dumps(report, indent=2) + "\n")
    encrypted = encrypt_password(os.environ["NOVA_QA_OWNER_PASSWORD"])
    session = str(uuid4())
    service = QueryService()
    metadata = None

    def check(name: str, condition: bool) -> None:
        report["steps"].append({"check": name, "passed": condition})
        if not condition:
            raise AssertionError(name)

    async def execute(
        sql: str, parameters: BoundParameters, role: str = "marketing", sink: RowSink | None = None,
    ):
        result = await service.execute(
            sql, principal, encrypted, role=role, database="analytics", max_rows=100,
            session_id=session, parameters=parameters, row_sink=sink,
        )
        check("governed parameter execution succeeded", result.success)
        check("execution identity and engine correlation retained",
              bool(result.nova_execution_id) and bool(result.engine_query_ids))
        return result

    class CollectedRows:
        fetch_batch_rows = 1

        def __init__(self):
            self.columns: list[str] = []
            self.values: list[list] = []

        async def begin(self, columns: list[str], column_types: tuple[str, ...]) -> None:
            self.columns = columns

        async def rows(self, rows: list[list]) -> None:
            if len(self.values) + len(rows) > 100:
                raise ValueError("Acceptance result exceeds its bound")
            self.values.extend(rows)

    def text(value: str, multiple: bool = False) -> BoundParameters:
        return BoundParameters(
            (ParameterSpec("city", "string", multiple=multiple),),
            (("city", ("Jakarta", "Bandung") if multiple else value),),
        )

    try:
        await db.init_system_pool()
        metadata = await open_process_metadata("query")
        result = await execute(
            "SELECT id, city, amount FROM analytics.sales WHERE city = :city", text(city),
        )
        check("Ranger row filter applies to the active principal",
              bool(result.rows) and {row[1] for row in result.rows} == {city})
        result = await execute(
            "SELECT id, city FROM analytics.sales WHERE city IN (:city)", text(city, True),
        )
        check("list binding cannot broaden Ranger visibility",
              bool(result.rows) and {row[1] for row in result.rows} == {city})
        streamed = CollectedRows()
        result = await execute(
            "SELECT id, city FROM analytics.sales WHERE city IN (:city)",
            text(city, True), sink=streamed,
        )
        check("streamed list binding retains Ranger visibility and does not buffer result rows",
              result.streamed and result.rows == [] and bool(streamed.values)
              and {row[1] for row in streamed.values} == {city}
              and result.row_count == len(streamed.values))
        canary = "ndv_" + uuid4().hex + "' OR 1=1; -- \\ %"
        result = await execute(
            "SELECT id, city FROM analytics.sales WHERE city = :city", text(canary),
        )
        check("injection-shaped text remains a value", result.rows == [])
        check("result metadata contains parameter names only",
              canary not in result.executed_sql and ":city" in result.executed_sql)
        values = BoundParameters(
            tuple(ParameterSpec(name, kind, precision=38, scale=12) if kind == "decimal"
                  else ParameterSpec(name, kind) for name, kind in (
                ("big", "integer"), ("amount", "decimal"), ("day", "date"),
                ("moment", "timestamp"), ("flag", "boolean"),
            )),
            (
                ("big", 2**63 - 1), ("amount", Decimal("12345678901234567890123456.123456789012")),
                ("day", date(2024, 2, 29)), ("moment", datetime(2024, 11, 3, 6, 30, tzinfo=UTC)),
                ("flag", False),
            ),
        )
        result = await execute(
            "SELECT CAST(:big AS BIGINT) AS big_value, "
            "CAST(:amount AS DECIMAL(38,12)) AS amount_value, "
            "CAST(:day AS DATE) AS day_value, CAST(:moment AS DATETIME) AS moment_value, "
            "CAST(:flag AS BOOLEAN) AS flag_value", values,
        )
        check("int64 decimal date timestamp and boolean retain their values", result.rows == [[
            2**63 - 1, Decimal("12345678901234567890123456.123456789012"), date(2024, 2, 29),
            datetime(2024, 11, 3, 13, 30), 0,
        ]])
        exact_rows = CollectedRows()
        exact = BoundParameters(
            (ParameterSpec("amount", "decimal", precision=38, scale=12),),
            (("amount", Decimal("12345678901234567890123456.123456789012")),),
        )
        result = await execute(
            "SELECT CAST(:amount AS DECIMAL(38,12)) AS amount_value", exact, sink=exact_rows,
        )
        check("streamed decimal preserves all 38 digits",
              result.streamed and result.rows == []
              and exact_rows.values == [[Decimal("12345678901234567890123456.123456789012")]])
        limited = CollectedRows()
        try:
            await service.execute(
                "SELECT :amount AS n UNION ALL SELECT :amount AS n", principal, encrypted,
                role="marketing", database="analytics", max_rows=1, session_id=session,
                parameters=exact, row_sink=limited,
            )
        except StarRocksError as error:
            check("stream overrun refuses the query without exposing a bound value",
                  str(error) == "Bound query exceeded its streaming result limit"
                  and len(limited.values) <= 1)
        else:
            check("stream overrun refuses the query without exposing a bound value", False)
        result = await execute("SELECT :city AS city_value", text(city))
        check("a fresh managed session succeeds after an overrun", result.rows == [[city]])
        result = await execute(
            "SELECT phone FROM analytics.customers WHERE :city <> '' ORDER BY id", text(city),
        )
        reference = await service.execute(
            "SELECT phone FROM analytics.customers ORDER BY id", principal, encrypted,
            role="marketing", database="analytics", max_rows=100, session_id=session,
        )
        check("parameter query matches the governed masked reference",
              reference.success and bool(result.rows) and result.rows == reference.rows)
        if principal == "alice":
            raw = await service.execute(
                "SELECT phone FROM analytics.customers ORDER BY id", principal, encrypted,
                role="regional_manager", database="analytics", max_rows=100, session_id=session,
            )
            check("authorized role comparison proves masked values differ from raw values",
                  raw.success and bool(raw.rows)
                  and not ({row[0] for row in raw.rows} & {row[0] for row in result.rows}))
            try:
                await execute(
                    "SELECT id FROM analytics.sales WHERE city = :city", text(city), "finance",
                )
            except StarRocksError as error:
                check("inactive role cannot borrow another role's source grant",
                      error.engine_code is not None and not 2000 <= error.engine_code < 3000)
            else:
                check("inactive role cannot borrow another role's source grant", False)
        actor = require_security_context(
            principal=principal, active_role="marketing", session_id=session,
            security_context_version=1, database="analytics",
        )
        with acting_as_sync(actor):
            history = await service.get_history(username=principal, limit=100)
        owned = [item for item in history["items"] if item["session_id"] == session]
        check("normal query audit is durable", len(owned) >= 5)
        check("parameter values absent from Nova history",
              canary not in json.dumps(owned, default=str))
        report["result"] = "PASS"
    except Exception as error:
        report.update(result="FAIL", error_type=type(error).__name__, error_location=[
            {"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
            for frame in traceback.extract_tb(error.__traceback__)[-3:]
        ])
    finally:
        await close_process_metadata(metadata, "query")
        await db.close_system_pool()
        report["scope"] = (
            "Direct QueryService with existing caller credentials and active roles; read-only. "
            "Includes Nova history checks. Engine audit/profile redaction is not tested."
        )
        output.write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--principal", choices=["alice", "bob"], required=True)
    parser.add_argument("--city", choices=["Jakarta", "Bandung"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(run(args.principal, args.city, args.output))
    print(json.dumps({"result": result["result"], "checks": len(result["steps"])}))
    raise SystemExit(0 if result["result"] == "PASS" else 1)
