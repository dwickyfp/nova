"""Exercise preview refusal/cancellation against the existing caller's engine session.

Only synthetic SELECT statements run. No tables, grants or metadata are changed.
The password is supplied through NOVA_QA_OWNER_PASSWORD, never a command argument.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from app.core.exceptions import NovaException
from app.modules.query.repository import QueryRepository
from app.modules.viz.contracts.datasets import DatasetField
from app.modules.viz.preview import PreviewRows


async def run(principal: str, role: str) -> dict:
    repository = QueryRepository()
    password = os.environ["NOVA_QA_OWNER_PASSWORD"]
    report: dict = {
        "result": "PASS",
        "scope": "Synthetic caller SELECT; no persistent writes",
        "checks": [],
    }

    def field(name: str) -> DatasetField:
        return DatasetField(
            field_id=name,
            name=name,
            source_column=name,
            logical_type="string",
            physical_type="varchar(4097)",
            semantic_role="dimension",
            nullable=True,
        )

    async def execute(sql: str, sink: PreviewRows):
        return await repository.execute_as_user(
            sql,
            username=principal,
            password=password,
            role=role,
            max_rows=3,
            row_sink=sink,
        )

    for name, sql, fields, expected in (
        (
            "long cell",
            "SELECT '" + "x" * 4097 + "' AS value UNION ALL SELECT 'pending'",
            (field("value"),),
            422,
        ),
        (
            "encoded byte budget",
            "WITH t AS (SELECT '"
            + "x" * 4096
            + "' AS v) SELECT "
            + ",".join(f"v AS f{i}" for i in range(270))
            + " FROM t",
            tuple(field(f"f{i}") for i in range(270)),
            413,
        ),
    ):
        result = {"name": name, "expected_status": expected, "passed": False}
        try:
            await execute(sql, PreviewRows(fields, 2))
        except Exception as error:
            result.update(
                error_type=type(error).__name__, status_code=getattr(error, "status_code", None)
            )
            result["passed"] = isinstance(error, NovaException) and error.status_code == expected
        report["checks"].append(result)

    entered = asyncio.Event()

    class PausedRows(PreviewRows):
        async def rows(self, rows: list[list]) -> None:
            entered.set()
            await asyncio.Event().wait()

    pending = asyncio.create_task(
        execute(
            "SELECT 'first' AS value UNION ALL SELECT 'pending'", PausedRows((field("value"),), 2)
        )
    )
    cancelled = {"name": "cancellation preserves CancelledError", "passed": False}
    try:
        await asyncio.wait_for(entered.wait(), timeout=15)
        pending.cancel()
        await asyncio.wait_for(pending, timeout=5)
    except asyncio.CancelledError:
        cancelled["passed"] = True
    except Exception as error:
        cancelled["error_type"] = type(error).__name__
    finally:
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
    report["checks"].append(cancelled)
    report["result"] = "PASS" if all(check["passed"] for check in report["checks"]) else "FAIL"
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--principal", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(run(args.principal, args.role))
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(result["result"])
    raise SystemExit(0 if result["result"] == "PASS" else 1)
