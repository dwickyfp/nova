import datetime as dt
import getpass
import json
import pathlib
import re
import sys
import time
from decimal import Decimal

import snowflake.connector

ROOT = pathlib.Path(__file__).parent
EVENTS = ROOT / "events.jsonl"


def serial(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    return str(value)


password = getpass.getpass("Snowflake password (hidden): ")
try:
    conn = snowflake.connector.connect(
        account="vd82948.ap-southeast-3.aws",
        user="dwickyferi",
        password=password,
        login_timeout=30,
        network_timeout=30,
        socket_timeout=10,
        client_session_keep_alive=False,
        ocsp_fail_open=False,
        session_parameters={
            "QUERY_TAG": "SNOWFLAKE_RESEARCH_20261013",
            "STATEMENT_TIMEOUT_IN_SECONDS": 60,
            "LOCK_TIMEOUT": 10,
        },
    )
except Exception as exc:
    message = str(exc).replace(password, "<redacted>")
    print(json.dumps({"connection": "FAILED", "error": message}), flush=True)
    sys.exit(1)
finally:
    del password

print(json.dumps({"connection": "CONNECTED", "connector_version": snowflake.connector.__version__}), flush=True)
try:
    for command in sys.stdin:
        command = command.strip()
        if command == "quit":
            break
        if not command.startswith("run "):
            continue
        suite_path = pathlib.Path(command[4:])
        cases = json.loads(suite_path.read_text())
        for case in cases:
            started = time.monotonic()
            event = {"id": case["id"], "sql": case["sql"], "time_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
            cursor = conn.cursor()
            try:
                cursor.execute(case["sql"])
                raw_rows = cursor.fetchmany(case.get("max_rows", 100)) if cursor.description else []
                rows = json.loads(json.dumps(raw_rows, default=serial))
                event.update({"query_id": cursor.sfqid, "columns": [c[0] for c in cursor.description or []], "rows": rows})
                if "expected_error" in case:
                    event.update(status="FAIL", reason="Expected an error, but statement succeeded")
                elif "expected_rows" in case and rows != case["expected_rows"]:
                    event.update(status="FAIL", reason="Result differs from expected_rows", expected_rows=case["expected_rows"])
                elif "expected_scalar" in case and (not rows or rows[0][0] != case["expected_scalar"]):
                    event.update(status="FAIL", reason="Result differs from expected_scalar", expected_scalar=case["expected_scalar"])
                else:
                    event["status"] = "OBSERVED" if case.get("observe_only") else "PASS"
            except Exception as exc:
                message = str(exc)
                expected = case.get("expected_error")
                event.update({"query_id": getattr(exc, "sfqid", None) or cursor.sfqid, "error": message, "errno": getattr(exc, "errno", None), "sqlstate": getattr(exc, "sqlstate", None)})
                event["status"] = "EXPECTED_ERROR" if expected and re.search(expected, message, re.I) else "ERROR"
            finally:
                cursor.close()
            event["elapsed_seconds"] = round(time.monotonic() - started, 3)
            with EVENTS.open("a") as handle:
                handle.write(json.dumps(event, default=serial) + "\n")
            brief = {key: event[key] for key in ("id", "status", "elapsed_seconds", "error", "reason") if key in event}
            if case.get("print_rows"):
                brief.update({"columns": event.get("columns"), "rows": event.get("rows")})
            print(json.dumps(brief, default=serial), flush=True)
        print(json.dumps({"suite_complete": str(suite_path), "cases": len(cases)}), flush=True)
finally:
    conn.close()
    print("CONNECTION_CLOSED", flush=True)
