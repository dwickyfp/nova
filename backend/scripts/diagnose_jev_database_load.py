"""Record database pressure without exposing query text or session credentials."""

import asyncio
import json
import re
import subprocess
from collections import Counter
from datetime import UTC, datetime

from tests.benchmark.jev_multidomain.environment import ARTIFACTS


async def main() -> None:
    from app.core.database import db

    result = {"at": datetime.now(UTC).isoformat()}
    await db.init_system_pool()
    try:
        async with db.system_conn() as connection, connection.cursor() as cursor:
            await cursor.execute("SHOW FULL PROCESSLIST")
            names = [column[0] for column in cursor.description]
            rows = [dict(zip(names, row, strict=True)) for row in await cursor.fetchall()]
            result["processes"] = [
                {
                    "command": row.get("Command"),
                    "seconds": row.get("Time"),
                    "statement": str(row.get("Info") or "").split(" ")[0][:12]
                    if re.match(r"^(SELECT|INSERT|UPDATE|DELETE|SHOW)\b", row.get("Info") or "")
                    else "other",
                    "benchmark_journal": "CONFIG_JEVBENCH_" in str(row.get("Info")),
                    "benchmark_data": "NOVA_JEV_BENCH_20260925" in str(row.get("Info")),
                }
                for row in rows
            ]
    finally:
        await db.close_system_pool()
    patterns = [
        "timeout", "timed out", "too many versions", "memory limit", "syntax error",
        "publish version", "queue is full", "cancelled", "canceled", "outofmemory",
    ]
    raw = subprocess.run(
        [
            "docker", "exec", "nova-starrocks-fe", "tail", "-n", "3000",
            "/opt/starrocks/fe/log/fe.warn.log",
        ],
        capture_output=True, text=True, check=False,
    )
    result["fe_warning_read_ok"] = raw.returncode == 0
    counts = Counter()
    for line in raw.stdout.lower().splitlines():
        counts.update(pattern for pattern in patterns if pattern in line)
    result["fe_recent_warning_patterns"] = dict(counts)
    output = ARTIFACTS / "database-load-snapshots.jsonl"
    with output.open("a") as handle:
        handle.write(json.dumps(result) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(main())
