"""Author and preview one parameterized SQL dataset through the normal HTTP API.

Uses existing QA identities (NOVA_QA_OWNER_PASSWORD, NOVA_QA_OUTSIDER_PASSWORD).
Run it through verify_native_source_identity_live --parameter-dataset-output: that
fixture owns the database, role and rows. This check creates one personal native
dataset and soft-deletes it; it changes no grants, policies or deployment state.
Ranger row filters under bound values are covered by verify_native_parameters_live.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from scripts.verify_native_documents_live import LiveCheck

SQL = (
    "SELECT id, city, amount, sale_day FROM {table} "
    "WHERE city IN (:cities) AND amount >= :min_amount AND sale_day >= :since"
)
PARAMETERS = [
    {"parameter_id": "cities", "type": "string", "multiple": True},
    {"parameter_id": "min_amount", "type": "decimal", "precision": 18, "scale": 2,
     "defaults": [{"type": "decimal", "value": "0"}],
     "minimum": {"type": "decimal", "value": "0"}},
    {"parameter_id": "since", "type": "date",
     "defaults": [{"type": "date", "value": "2024-01-01"}]},
]


def cities(*names: str) -> dict[str, Any]:
    return {"parameter_id": "cities", "values": [{"type": "string", "value": n} for n in names]}


async def run(args: argparse.Namespace) -> dict[str, Any]:
    identity = str(uuid4())
    path = f"/api/v1/viz/datasets/{identity}"
    source = {"kind": "sql", "database": args.database, "sql": SQL.format(table=args.table)}
    report: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(), "resource_id": identity,
        "owner": args.owner,
        "scope": "parameterized SQL dataset HTTP API, real StarRocks/Ranger and PG17 metadata",
        "result": "FAIL", "cleanup": "NOT_RUN",
    }
    canary = "ndv_" + uuid4().hex + "' OR 1=1; -- %"
    tokens: list[str] = []
    async with httpx.AsyncClient(base_url=args.base_url, timeout=60) as client:
        probe = LiveCheck(client)
        try:
            for username, variable in ((args.owner, "NOVA_QA_OWNER_PASSWORD"),
                                       (args.outsider, "NOVA_QA_OUTSIDER_PASSWORD")):
                response = await client.post("/api/v1/auth/login", json={
                    "username": username, "password": os.environ[variable]})
                probe.check("QA login", response.status_code == 200)
                login = response.json()
                probe.check("QA admitted", login["status"] == "AUTHENTICATED")
                probe.check("non-admin role", login["active_role"] != "ACCOUNTADMIN")
                tokens.append(login["access_token"])
            owner, outsider = tokens
            if args.role:
                switched = await probe.request("activate fixture role", "POST",
                                               "/api/v1/auth/switch-role", owner,
                                               body={"role": args.role})
                probe.check("exact active role", switched.json()["active_role"] == args.role)

            await probe.request(
                "undeclared named parameter refused", "POST", "/api/v1/viz/datasets/inspect",
                owner, status=422, body={"dataset_id": identity, "source": source})
            inspected = await probe.request(
                "inspect with declarations", "POST", "/api/v1/viz/datasets/inspect", owner,
                body={"dataset_id": identity, "source": source, "parameters": PARAMETERS})
            schema = inspected.json()
            columns = [f["source_column"] for f in schema["fields"]]
            probe.check("schema from analyzer without data rows",
                        columns == ["id", "city", "amount", "sale_day"]
                        and "rows" not in schema)
            declared = [p["parameter_id"] for p in schema["parameters"]]
            probe.check("declarations echoed exactly",
                        declared == ["cities", "min_amount", "since"]
                        and schema["parameters"][1]["defaults"] == PARAMETERS[1]["defaults"])
            body = {"dataset_id": identity, "source": source, "parameters": PARAMETERS,
                    "workspace_id": "personal", "title": "Parameterized sales acceptance",
                    "expected_schema_fingerprint": schema["schema_fingerprint"]}
            await probe.request(
                "changed declarations need a new inspection", "POST", "/api/v1/viz/datasets",
                owner, operation=str(uuid4()), status=409,
                body={**body, "parameters": [
                    PARAMETERS[0], {**PARAMETERS[1], "scale": 4}, PARAMETERS[2]]})
            await probe.request("create", "POST", "/api/v1/viz/datasets", owner,
                                operation=str(uuid4()), status=201, body=body)
            draft = await probe.request("draft", "GET", path + "?draft=true", owner)
            probe.check("stored declarations", draft.json()["definition"]["parameters"]
                        == schema["parameters"])
            preview_path = path + "/preview"

            async def preview(name: str, parameters: list[dict[str, Any]], status: int = 200,
                              token: str = owner) -> dict[str, Any]:
                response = await probe.request(
                    name, "POST", preview_path, token, status=status,
                    body={"revision": 1, "draft": True, "limit": 200, "parameters": parameters})
                probe.check(f"{name}: no stored value echoed", canary not in response.text)
                return response.json() if status == 200 else {}

            await preview("required value missing", [], status=422)
            await preview("wrong value type", [{"parameter_id": "cities",
                                                "values": [{"type": "integer", "value": "1"}]}],
                          status=422)
            await preview("below declared minimum", [cities("Jakarta"), {
                "parameter_id": "min_amount", "values": [{"type": "decimal", "value": "-1"}]}],
                status=422)
            await preview("unknown parameter", [cities("Jakarta"), {
                "parameter_id": "region", "values": [{"type": "string", "value": "x"}]}],
                status=422)
            page = await preview("scalar list value with default minimum", [cities("Jakarta")])
            probe.check("one list value selects only matching rows",
                        [row[0]["value"] for row in page["rows"]] == ["1", "3"]
                        and {row[1]["value"] for row in page["rows"]} == {"Jakarta"})
            page = await preview("list value with default minimum", [cities("Jakarta", "Bandung")])
            probe.check("multiple list values select exactly their rows",
                        [row[0]["value"] for row in page["rows"]] == ["1", "2", "3"])
            probe.check("decimal values keep their declared scale",
                        [row[2]["value"] for row in page["rows"]] == ["10.50", "20.00", "30.25"])
            probe.check("date values keep their calendar day",
                        [row[3] for row in page["rows"]] == [
                            {"type": "date", "value": day}
                            for day in ("2024-02-28", "2024-02-29", "2024-03-01")])
            page = await preview("explicit leap-day date", [cities("Jakarta", "Bandung"), {
                "parameter_id": "since", "values": [{"type": "date", "value": "2024-02-29"}]}])
            probe.check("inclusive typed date comparison",
                        [row[0]["value"] for row in page["rows"]] == ["2", "3"])
            await preview("invalid calendar date", [cities("Jakarta"), {
                "parameter_id": "since", "values": [{"type": "date", "value": "2023-02-29"}]}],
                status=422)
            page = await preview("explicit decimal value", [cities("Jakarta", "Bandung"), {
                "parameter_id": "min_amount", "values": [{"type": "decimal", "value": "20.00"}]}])
            probe.check("inclusive decimal comparison",
                        [row[0]["value"] for row in page["rows"]] == ["2", "3"])
            high = await preview("explicit minimum", [cities("Jakarta", "Bandung"), {
                "parameter_id": "min_amount",
                "values": [{"type": "decimal", "value": "999999999"}]}])
            probe.check("explicit value overrides default", high["rows"] == [])
            injected = await preview("injection-shaped text", [cities(canary)])
            probe.check("injection-shaped text stays a value", injected["rows"] == [])
            await preview("personal dataset hidden from outsider", [cities("Jakarta")],
                          status=404, token=outsider)
            history = await probe.request("caller query history", "GET",
                                          "/api/v1/query/history?limit=100", owner)
            probe.check("parameter values absent from history", canary not in history.text)
            report["result"] = "PASS"
        except Exception as error:
            report["error_type"] = type(error).__name__
            report["error"] = str(error)[:200]
        finally:
            if tokens:
                try:
                    draft_response = await client.get(
                        path + "?draft=true", headers={"Authorization": f"Bearer {tokens[0]}"})
                    if draft_response.status_code == 200:
                        key = str(uuid4())
                        revision = draft_response.json()["revision"]
                        await probe.request(
                            "delete owned dataset", "DELETE", path, tokens[0], operation=key,
                            revision=revision,
                            body={"operation_id": key, "expected_revision": revision})
                        await probe.request("deleted dataset hidden", "GET", path, tokens[0],
                                            status=404)
                        report["cleanup"] = "SOFT_DELETED"
                    elif draft_response.status_code == 404:
                        report["cleanup"] = "NOT_CREATED"
                    else:
                        raise AssertionError("Cleanup observation failed")
                except Exception as error:
                    report.update(cleanup="UNVERIFIED", result="FAIL",
                                  cleanup_error_type=type(error).__name__)
            for token in tokens:
                try:
                    await probe.request("logout", "POST", "/api/v1/auth/logout", token)
                except Exception as error:
                    report.update(result="FAIL", logout_error_type=type(error).__name__)
            report["steps"] = probe.steps
            report["finished_at"] = datetime.now(UTC).isoformat()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5173")
    parser.add_argument("--owner", required=True)
    parser.add_argument("--outsider", required=True)
    parser.add_argument("--role")
    parser.add_argument("--database", required=True)
    parser.add_argument("--table", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(run(args))
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"result": report["result"], "cleanup": report["cleanup"],
                      "checks": len(report["steps"])}))
    raise SystemExit(0 if report["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
