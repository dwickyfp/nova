from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from .contracts import Outcome
from .geospatial import ORIGIN_POINT_BYTES
from .runtime import Target, mysql_cli


async def compatibility_cases(target: Target, database: str, stages: list[dict]) -> list[Outcome]:
    checks = [
        ("literal", "SELECT 42;", "42", None),
        ("unicode_null", "SELECT 'Jakarta 🐘',NULL;", "Jakarta 🐘\tNULL", None),
        ("session_script", "SET @nova_cli='saved'; SELECT @nova_cli; SELECT 2;", "saved\n2", None),
        (
            "variable_expression",
            "SET @nova_cli=1; SET @nova_cli=@nova_cli+1; SELECT @nova_cli;",
            "2",
            None,
        ),
        (
            "variable_snapshot",
            "SET @nova_cli=rand(); SELECT @nova_cli=@nova_cli;",
            "1",
            None,
        ),
        (
            "types",
            "SELECT CAST(12.34 AS DECIMAL(10,2)),DATE '2024-02-29',hex(unhex('00ff'));",
            "12.34\t2024-02-29\t00FF",
            None,
        ),
        (
            "time_duration",
            "SELECT timediff('2024-01-03 03:04:05','2024-01-01 00:00:00'),"
            "timediff('2024-01-01 00:00:00','2024-01-02 01:02:03');",
            "51:04:05\t-25:02:03",
            None,
        ),
        (
            "geometry_wkt",
            "SELECT st_astext(st_point(106.8272,-6.1754)),"
            "st_x(st_point(106.8272,-6.1754)),st_y(st_point(106.8272,-6.1754));",
            "POINT (106.8272 -6.1754)\t106.8272\t-6.1754",
            None,
        ),
        (
            "geometry_binary",
            "SELECT st_point(0,0);",
            "0x" + ORIGIN_POINT_BYTES.hex().upper(),
            None,
        ),
        ("missing_table", "SELECT * FROM nova_sql_absent_table; SELECT 2;", "", "ERROR 1064"),
    ]
    if stages:
        checks.append(
            (
                "stage_csv",
                f"SELECT * FROM @{stages[0]['name']}.values.csv ORDER BY 1;",
                "1\talpha\n2\tbeta",
                None,
            )
        )
    outcomes = []
    for name, sql, expected, error in checks:
        result = Outcome(
            "mysql_cli." + name,
            "protocol",
            "FAIL",
            target.safe(sql),
            verification_kind="refusal" if error else "positive",
        )
        try:
            code, stdout, stderr = await mysql_cli(target, sql, database=database)
            result.observed_rows = {"exit_code": code, "stdout": stdout, "stderr": stderr}
            assert stdout.strip() == expected, "MySQL client returned unexpected values"
            if error:
                assert code != 0 and error in stderr, "MySQL client did not preserve the refusal"
            else:
                assert code == 0, f"MySQL client exit {code}: {stderr}"
            result.status = "PASS"
        except Exception as exc:
            result.detail = target.failure(exc)
        outcomes.append(result)
    result = Outcome(
        "mysql_cli.authentication_rejected", "protocol", "FAIL", verification_kind="refusal"
    )
    try:
        denied = replace(target, password=uuid4().hex)
        code, stdout, stderr = await mysql_cli(denied, "SELECT 42;", database=database)
        result.observed_rows = {"exit_code": code, "stdout": stdout, "stderr": stderr}
        assert code != 0 and "ERROR 1045" in stderr and not stdout.strip()
        result.status = "PASS"
    except Exception as exc:
        result.detail = target.failure(exc)
    outcomes.append(result)
    return outcomes
