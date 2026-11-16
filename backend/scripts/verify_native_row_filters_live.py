"""One shared native dataset, read by different people under their own roles.

``alice``, as regional_manager, shares a workspace with the ``marketing`` role,
saves a SQL dataset over ``analytics.sales`` there and publishes it. Then the
same governed aggregate runs through the native chart query API for each
viewer. Ranger's row filter decides the rows: the seed gives ``alice`` both
cities as regional_manager, Jakarta as marketing, and ``bob`` Bandung as
marketing. No administrator is involved.

Credentials come from NOVA_QA_OWNER_PASSWORD (alice) and
NOVA_QA_OUTSIDER_PASSWORD (bob). Everything created is removed afterwards.
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

SQL = "SELECT city, amount FROM analytics.sales"


class LiveCheck:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.steps: list[dict[str, Any]] = []

    def check(self, name: str, condition: bool, **detail: Any) -> None:
        self.steps.append({"check": name, "passed": condition, **detail})
        if not condition:
            raise AssertionError(name)

    async def request(self, name: str, method: str, path: str, token: str, *, status: int = 200,
                      body: Any = None, operation: str | None = None,
                      revision: int | None = None) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}"}
        if operation:
            headers["Idempotency-Key"] = operation
        if revision is not None:
            headers["If-Match"] = f'"{revision}"'
        response = await self.client.request(method, path, headers=headers, json=body)
        self.steps.append({"request": name, "status": response.status_code,
                           "expected_status": status})
        self.check(name, response.status_code == status)
        return response


async def login(probe: LiveCheck, username: str, variable: str) -> str:
    response = await probe.client.post("/api/v1/auth/login", json={
        "username": username, "password": os.environ[variable]})
    probe.check(f"{username} login", response.status_code == 200)
    probe.check(f"{username} admitted", response.json()["status"] == "AUTHENTICATED")
    return str(response.json()["access_token"])


async def run(args: argparse.Namespace) -> dict[str, Any]:
    dataset = str(uuid4())
    path = f"/api/v1/viz/datasets/{dataset}"
    report: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(), "resource_id": dataset,
        "scope": "shared native dataset read by three people under Ranger row filters",
        "result": "FAIL", "cleanup": "NOT_RUN",
    }
    tokens: dict[str, str] = {}
    space_id: str | None = None
    async with httpx.AsyncClient(base_url=args.base_url, timeout=60) as client:
        probe = LiveCheck(client)
        try:
            # Two sessions for alice: one authors as regional_manager, one views as marketing.
            tokens["author"] = await login(probe, "alice", "NOVA_QA_OWNER_PASSWORD")
            tokens["alice"] = await login(probe, "alice", "NOVA_QA_OWNER_PASSWORD")
            tokens["bob"] = await login(probe, "bob", "NOVA_QA_OUTSIDER_PASSWORD")
            admin = tokens["author"]
            author = await probe.request("author uses regional_manager", "POST",
                                         "/api/v1/auth/switch-role", admin,
                                         body={"role": "regional_manager"})
            probe.check("author role is regional_manager",
                        author.json()["active_role"] == "regional_manager")
            for viewer in ("alice", "bob"):
                switched = await probe.request(f"{viewer} uses marketing", "POST",
                                               "/api/v1/auth/switch-role", tokens[viewer],
                                               body={"role": "marketing"})
                probe.check(f"{viewer} active role is marketing",
                            switched.json()["active_role"] == "marketing")

            created = await probe.request(
                "shared workspace", "POST", "/api/v1/workspaces/spaces", admin, status=201,
                body={"name": f"Row filter acceptance {dataset[:8]}"})
            space_id = str(created.json()["space"]["id"])
            await probe.request("share with marketing", "PUT",
                                f"/api/v1/workspaces/spaces/{space_id}/shares", admin,
                                body={"shares": [{"role": "marketing", "permission": "view"}]})

            source = {"kind": "sql", "database": "analytics", "sql": SQL}
            inspected = await probe.request("inspect", "POST", "/api/v1/viz/datasets/inspect",
                                            admin, body={"dataset_id": dataset, "source": source})
            schema = inspected.json()
            fields = {f["source_column"]: f["field_id"] for f in schema["fields"]}
            probe.check("schema without rows", set(fields) == {"city", "amount"}
                        and "rows" not in schema)
            await probe.request("create in shared workspace", "POST", "/api/v1/viz/datasets",
                                admin, status=201, operation=str(uuid4()), body={
                                    "dataset_id": dataset, "source": source,
                                    "workspace_id": space_id, "title": "Sales by city",
                                    "expected_schema_fingerprint": schema["schema_fingerprint"]})
            key = str(uuid4())
            await probe.request("publish", "POST", path + "/publish", admin, operation=key,
                                revision=1, body={"operation_id": key, "expected_revision": 1})

            query = {"revision": 1, "draft": False,
                     "dimensions": [{"field_id": fields["city"]}],
                     "measures": [{"field_id": fields["amount"], "aggregation": "sum"}],
                     "sort": [{"column": "d0", "direction": "asc"}]}

            async def rows(name: str, token: str) -> list[list[str]]:
                response = await probe.request(name, "POST", path + "/query", token, body=query)
                return [[cell["value"] for cell in row] for row in response.json()["rows"]]

            everyone = await rows("author query as regional_manager", admin)
            probe.check("regional_manager sees both cities",
                        everyone == [["Bandung", "600.00"], ["Jakarta", "400.00"]], rows=everyone)
            alice = await rows("alice query as marketing", tokens["alice"])
            probe.check("alice sees only Jakarta", alice == [["Jakarta", "400.00"]], rows=alice)
            bob = await rows("bob query as marketing", tokens["bob"])
            probe.check("bob sees only Bandung", bob == [["Bandung", "600.00"]], rows=bob)

            # The same person, another role: the active role decides the rows.
            await probe.request("alice uses regional_manager", "POST", "/api/v1/auth/switch-role",
                                tokens["alice"], body={"role": "regional_manager"})
            widened = await rows("alice query as regional_manager", tokens["alice"])
            probe.check("the same viewer sees more with a wider role",
                        widened == [["Bandung", "600.00"], ["Jakarta", "400.00"]], rows=widened)

            # A role outside the share cannot open the dataset at all.
            await probe.request("alice uses finance", "POST", "/api/v1/auth/switch-role",
                                tokens["alice"], body={"role": "finance"})
            await probe.request("finance cannot open the shared dataset", "POST", path + "/query",
                                tokens["alice"], body=query, status=404)
            report["result"] = "PASS"
        except Exception as error:
            # Exceptions can carry server payloads; keep only the type and a short message.
            report["error_type"] = type(error).__name__
            report["error"] = str(error)[:200]
        finally:
            admin_token = tokens.get("author")
            if admin_token:
                try:
                    draft = await client.get(path + "?draft=true",
                                             headers={"Authorization": f"Bearer {admin_token}"})
                    if draft.status_code == 200:
                        key = str(uuid4())
                        revision = draft.json()["revision"]
                        await probe.request("delete dataset", "DELETE", path, admin_token,
                                            operation=key, revision=revision,
                                            body={"operation_id": key,
                                                  "expected_revision": revision})
                    if space_id:
                        await probe.request("delete workspace", "DELETE",
                                            f"/api/v1/workspaces/spaces/{space_id}", admin_token)
                    report["cleanup"] = "REMOVED"
                except Exception as error:
                    report.update(cleanup="UNVERIFIED", result="FAIL",
                                  cleanup_error_type=type(error).__name__)
            for name, token in tokens.items():
                try:
                    await client.post("/api/v1/auth/logout",
                                      headers={"Authorization": f"Bearer {token}"})
                except Exception:
                    report.setdefault("logout_errors", []).append(name)
            report["steps"] = probe.steps
            report["finished_at"] = datetime.now(UTC).isoformat()
    return report


async def view(args: argparse.Namespace) -> dict[str, Any]:
    """Viewer checks on a dataset a steward already shared with marketing and published."""
    path = f"/api/v1/viz/datasets/{args.dataset}"
    report: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(), "resource_id": args.dataset,
        "scope": "steward-published native dataset read by QA viewers under Ranger row filters",
        "result": "FAIL", "cleanup": "BY_AUTHOR",
    }
    query = {"revision": 1, "draft": False,
             "dimensions": [{"field_id": args.city_field}],
             "measures": [{"field_id": args.amount_field, "aggregation": "sum"}],
             "sort": [{"column": "d0", "direction": "asc"}]}
    tokens: dict[str, str] = {}
    async with httpx.AsyncClient(base_url=args.base_url, timeout=60) as client:
        probe = LiveCheck(client)

        async def rows(name: str, token: str) -> list[list[str]]:
            response = await probe.request(name, "POST", path + "/query", token, body=query)
            return [[cell["value"] for cell in row] for row in response.json()["rows"]]

        try:
            tokens["alice"] = await login(probe, "alice", "NOVA_QA_OWNER_PASSWORD")
            tokens["bob"] = await login(probe, "bob", "NOVA_QA_OUTSIDER_PASSWORD")
            for viewer in ("alice", "bob"):
                switched = await probe.request(f"{viewer} uses marketing", "POST",
                                               "/api/v1/auth/switch-role", tokens[viewer],
                                               body={"role": "marketing"})
                probe.check(f"{viewer} active role is marketing",
                            switched.json()["active_role"] == "marketing")
            alice = await rows("alice query as marketing", tokens["alice"])
            probe.check("alice sees only Jakarta", alice == [["Jakarta", "400.00"]], rows=alice)
            bob = await rows("bob query as marketing", tokens["bob"])
            probe.check("bob sees only Bandung", bob == [["Bandung", "600.00"]], rows=bob)
            await probe.request("alice uses regional_manager", "POST", "/api/v1/auth/switch-role",
                                tokens["alice"], body={"role": "regional_manager"})
            widened = await rows("alice query as regional_manager", tokens["alice"])
            probe.check("the same viewer sees more with a wider role",
                        widened == [["Bandung", "600.00"], ["Jakarta", "400.00"]], rows=widened)
            await probe.request("alice uses finance", "POST", "/api/v1/auth/switch-role",
                                tokens["alice"], body={"role": "finance"})
            await probe.request("a role outside the share cannot open it", "POST",
                                path + "/query", tokens["alice"], body=query, status=404)
            report["result"] = "PASS"
        except Exception as error:
            report["error_type"] = type(error).__name__
            report["error"] = str(error)[:200]
        finally:
            for name, token in tokens.items():
                try:
                    await client.post("/api/v1/auth/logout",
                                      headers={"Authorization": f"Bearer {token}"})
                except Exception:
                    report.setdefault("logout_errors", []).append(name)
            report["steps"] = probe.steps
            report["finished_at"] = datetime.now(UTC).isoformat()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5173")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset", help="View a dataset a steward already shared and published")
    parser.add_argument("--city-field")
    parser.add_argument("--amount-field")
    args = parser.parse_args()
    if args.dataset and not (args.city_field and args.amount_field):
        parser.error("--dataset needs --city-field and --amount-field")
    report = asyncio.run(view(args) if args.dataset else run(args))
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"result": report["result"], "cleanup": report["cleanup"],
                      "error": report.get("error")}))
    raise SystemExit(0 if report["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
