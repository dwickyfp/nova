"""Schema acceptance through Nova HTTP; inserts one row in the owned source fixture."""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime
from typing import Any

import httpx

from app.modules.query.repository import QueryResult
from app.sql_frontend.binding.starrocks import query_output_schema
from app.sql_frontend.binding.types import SqlType
from scripts.verify_native_documents_live import LiveCheck


async def run(args: argparse.Namespace) -> dict[str, Any]:
    report: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "result": "FAIL",
        "scope": "EXPLAIN SCHEMA through authenticated HTTP and governed pinned FE",
        "cleanup": "NOT_RUN",
    }
    token = ""
    async with httpx.AsyncClient(base_url=args.base_url, timeout=45) as client:
        probe = LiveCheck(client)

        async def execute(name: str, sql: str, *, success: bool = True):
            response = await probe.request(
                name,
                "POST",
                "/api/v1/query/execute",
                token,
                body={"sql": sql, "database": args.database, "max_rows": 512},
            )
            results = response.json()
            probe.check(name + " single result", isinstance(results, list) and len(results) == 1)
            result = results[0]
            probe.check(name + " outcome", result["success"] is success)
            if success:
                report.setdefault("schemas", []).append({"check": name, "rows": result["rows"]})
                probe.check(
                    name + " execution identity",
                    bool(result["nova_execution_id"]) and bool(result["engine_query_ids"]),
                )
                return query_output_schema(
                    QueryResult(columns=result["columns"], rows=result["rows"])
                )
            return None

        try:
            login = await client.post(
                "/api/v1/auth/login",
                json={
                    "username": args.owner,
                    "password": os.environ["NOVA_QA_OWNER_PASSWORD"],
                },
            )
            probe.check("login", login.status_code == 200)
            body = login.json()
            probe.check("admitted", body["status"] == "AUTHENTICATED")
            token = body["access_token"]
            await execute(
                "inactive role denied", "EXPLAIN SCHEMA SELECT * FROM source_view", success=False
            )
            switched = await probe.request(
                "activate fixture role",
                "POST",
                "/api/v1/auth/switch-role",
                token,
                body={"role": args.role},
            )
            probe.check("exact active role", switched.json()["active_role"] == args.role)
            columns = await execute(
                "empty view", "EXPLAIN SCHEMA SELECT id, amount FROM source_view"
            )
            probe.check(
                "empty source precision",
                columns is not None
                and columns[0].sql_type == SqlType("INTEGER", bits=64)
                and columns[1].sql_type == SqlType("DECIMAL", precision=38, scale=12)
                and columns[1].nullable is True,
            )
            columns = await execute(
                "computed output",
                "EXPLAIN SCHEMA WITH x AS "
                "(SELECT id, amount FROM source_view) SELECT CAST(id + 1 AS BIGINT) AS next_id, "
                "CAST(amount * 2 AS DECIMAL(38,12)) AS doubled, "
                "id > 0 AS positive FROM x WHERE 1=0",
            )
            probe.check(
                "computed output retains declared types",
                columns is not None
                and [column.name for column in columns] == ["next_id", "doubled", "positive"]
                and columns[0].sql_type == SqlType("INTEGER", bits=64)
                and columns[1].sql_type == SqlType("DECIMAL", precision=38, scale=12)
                and columns[2].sql_type == SqlType("BOOLEAN"),
            )
            join = (
                "SELECT l.id AS left_id, r.id AS right_id FROM source_table l "
                "LEFT JOIN source_table r ON l.id + 1 = r.id"
            )
            empty_join = await execute("empty outer join", "EXPLAIN SCHEMA " + join)
            inserted = await probe.request(
                "seed owned join row",
                "POST",
                "/api/v1/query/execute",
                token,
                body={"sql": "INSERT INTO source_table VALUES (1, 2.0)", "database": args.database},
            )
            probe.check("owned row inserted", inserted.json()[0]["success"] is True)
            observed = await probe.request(
                "observe unmatched join",
                "POST",
                "/api/v1/query/execute",
                token,
                body={"sql": join, "database": args.database},
            )
            probe.check(
                "unmatched join returns a null",
                observed.json()[0]["success"] is True and observed.json()[0]["rows"] == [[1, None]],
            )
            columns = await execute(
                "outer join",
                "EXPLAIN SCHEMA " + join,
            )
            probe.check(
                "outer join introduces nullability",
                columns is not None
                and columns[0].nullable is False
                and columns[1].nullable is True,
            )
            probe.check("schema independent of source rows", columns == empty_join)
            columns = await execute(
                "typed null and literals",
                "EXPLAIN SCHEMA SELECT "
                "CAST(NULL AS BIGINT) AS optional, CAST(TRUE AS BOOLEAN) AS enabled, "
                "CAST('2024-02-29' AS DATE) AS day_value, "
                "CAST('2024-02-29 12:00:00' AS DATETIME) AS time_value, "
                "CAST('12345678901234567890123456.123456789012' AS DECIMAL(38,12)) AS amount",
            )
            probe.check(
                "typed literals have engine schema",
                columns is not None
                and columns[0].nullable is True
                and columns[1].sql_type == SqlType("BOOLEAN")
                and columns[2].sql_type == SqlType("DATE")
                and columns[3].sql_type == SqlType("DATETIME")
                and columns[4].sql_type == SqlType("DECIMAL", precision=38, scale=12),
            )
            columns = await execute(
                "aggregate output", "EXPLAIN SCHEMA SELECT count(*) AS n FROM source_table"
            )
            probe.check(
                "aggregate output type",
                columns is not None and columns[0].sql_type == SqlType("INTEGER", bits=64),
            )
            for name, sql in [
                ("DML refused", "EXPLAIN SCHEMA DELETE FROM source_table"),
                ("side effect refused", "EXPLAIN SCHEMA SELECT sleep(1) AS result"),
            ]:
                await execute(name, sql, success=False)
            refused = await probe.request(
                "external output refused",
                "POST",
                "/api/v1/query/execute",
                token,
                status=403,
                body={
                    "sql": "EXPLAIN SCHEMA SELECT 1 INTO OUTFILE 's3://untrusted/result'",
                    "database": args.database,
                },
            )
            probe.check("export refusal returns no rows", "rows" not in refused.json())
            retained = await probe.request(
                "fixture unchanged by refused mutation",
                "POST",
                "/api/v1/query/execute",
                token,
                body={"sql": "SELECT count(*) AS n FROM source_table", "database": args.database},
            )
            probe.check(
                "fixture row retained",
                retained.json()[0]["success"] is True and retained.json()[0]["rows"] == [[1]],
            )
            report["result"] = "PASS"
        except Exception as error:
            report["error_type"] = type(error).__name__
        finally:
            if token:
                response = await client.post(
                    "/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"}
                )
                report["cleanup"] = "PASS" if response.status_code == 200 else "FAIL"
            report["steps"] = probe.steps
            if report["cleanup"] == "FAIL":
                report["result"] = "FAIL"
    return report
