"""Exercise live native metadata with two existing, non-admin QA identities.

Credentials come from NOVA_QA_OWNER_PASSWORD and NOVA_QA_OUTSIDER_PASSWORD.
Only a newly generated personal dashboard is mutated. Cleanup uses its normal
soft-delete API; the service retains its audit history and tombstone.
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


class LiveCheck:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.steps: list[dict[str, Any]] = []

    def check(self, name: str, condition: bool, **detail: Any) -> None:
        self.steps.append({"check": name, "passed": condition, **detail})
        if not condition:
            raise AssertionError(name)

    async def request(
        self, name: str, method: str, path: str, token: str, *, status: int = 200,
        body: Any = None, operation: str | None = None, revision: int | None = None,
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        if operation:
            headers["Idempotency-Key"] = operation
        if revision is not None:
            headers["If-Match"] = f'"{revision}"'
        response = await self.client.request(method, path, headers=headers, json=body)
        self.steps.append({"request": name, "status": response.status_code,
                           "expected_status": status})
        self.check(name, response.status_code == status)
        return response


async def run(args: argparse.Namespace) -> dict[str, Any]:
    identity = str(uuid4())
    document = {"dashboard_id": identity, "title": "Native document acceptance",
                "pages": [{"page_id": str(uuid4()), "title": "Overview"}]}
    path = f"/api/v1/viz/dashboards/{identity}"
    report: dict[str, Any] = {"started_at": datetime.now(UTC).isoformat(),
                              "resource_id": identity, "scope": "live document metadata",
                              "result": "FAIL", "cleanup": "NOT_RUN"}
    tokens: list[str] = []
    created = False
    async with httpx.AsyncClient(
        base_url=args.base_url, timeout=30, follow_redirects=False,
    ) as client:
        probe = LiveCheck(client)
        try:
            for username, variable in ((args.owner, "NOVA_QA_OWNER_PASSWORD"),
                                       (args.outsider, "NOVA_QA_OUTSIDER_PASSWORD")):
                response = await client.post("/api/v1/auth/login", json={
                    "username": username, "password": os.environ[variable],
                })
                probe.check("QA login", response.status_code == 200)
                login = response.json()
                probe.check("QA identity admitted", login["status"] == "AUTHENTICATED")
                tokens.append(login["access_token"])
                probe.check("QA role is not administrator", login["active_role"] != "ACCOUNTADMIN")
            owner, outsider = tokens
            await probe.request("anonymous refusal", "GET", path, "", status=401)
            create_key = str(uuid4())
            receipt = await probe.request(
                "create", "POST", "/api/v1/viz/dashboards", owner, status=201,
                body={"workspace_id": "personal", "definition": document}, operation=create_key,
            )
            created = True
            replay = await probe.request(
                "create replay", "POST", "/api/v1/viz/dashboards", owner, status=201,
                body={"workspace_id": "personal", "definition": document}, operation=create_key,
            )
            probe.check("same command has same receipt", replay.json() == receipt.json())
            draft = await probe.request("owner draft", "GET", path + "?draft=true", owner)
            probe.check("draft revision and cache boundary", draft.headers["etag"] == '"1"'
                        and draft.headers["cache-control"] == "no-store")
            await probe.request("unpublished refusal", "GET", path, owner, status=404)
            await probe.request("personal draft isolation", "GET", path + "?draft=true",
                                outsider, status=404)
            foreign_receipt = await probe.request(
                "receipt isolation", "GET", f"/api/v1/viz/operations/{create_key}",
                outsider, status=404,
            )
            probe.check("refusal contains no title", document["title"] not in foreign_receipt.text)
            publish_key = str(uuid4())
            await probe.request("publish", "POST", path + "/publish", owner,
                                operation=publish_key, revision=1,
                                body={"operation_id": publish_key, "expected_revision": 1})
            publication = await probe.request("publication", "GET", path, owner)
            publication_id = publication.json()["publication_id"]
            probe.check("publication bound to revision", publication_id is not None
                        and publication.json()["revision"] == 1)
            await probe.request("personal publication isolation", "GET", path, outsider, status=404)
            async def edit(title: str) -> httpx.Response:
                return await client.patch(path + "/draft", headers={
                    "Authorization": f"Bearer {owner}", "Idempotency-Key": str(uuid4()),
                    "If-Match": '"1"',
                }, json={**document, "title": title})
            outcomes = await asyncio.gather(edit("First editor"), edit("Second editor"))
            probe.check("concurrent CAS has one winner",
                        sorted(item.status_code for item in outcomes) == [200, 409])
            unchanged = await probe.request("retained publication", "GET", path, owner)
            probe.check("draft edits preserve publication", unchanged.json()["definition"]
                        == publication.json()["definition"])
            latest = await probe.request("latest draft", "GET", path + "?draft=true", owner)
            probe.check("single committed revision", latest.json()["revision"] == 2)
            restore_key = str(uuid4())
            await probe.request("restore", "POST", path + "/restore", owner,
                                operation=restore_key, revision=2, body={
                                    "operation_id": restore_key, "expected_revision": 2,
                                    "publication_id": publication_id,
                                })
            restored = await probe.request("restored draft", "GET", path + "?draft=true", owner)
            probe.check("restore creates new draft", restored.json()["revision"] == 3
                        and restored.json()["definition"] == publication.json()["definition"])
            history = await probe.request("history", "GET", path + "/revisions", owner)
            probe.check("history retained", [item["revision"]
                        for item in history.json()] == [3, 2, 1])
            events = await probe.request("event replay", "GET", path + "/events", owner)
            probe.check("only committed events", [item["event_kind"]
                        for item in events.json()["events"]]
                        == ["create", "publish", "edit", "restore"])
            report["result"] = "PASS"
        except Exception as error:
            # Exceptions can carry server SQL or authentication payloads.
            report["error_type"] = type(error).__name__
        finally:
            if tokens:
                try:
                    current = await client.get(path + "?draft=true", headers={
                        "Authorization": f"Bearer {tokens[0]}",
                    })
                    # Reconcile an unknown create outcome before attempting cleanup.
                    if current.status_code == 200:
                        revision = current.json()["revision"]
                        operation = str(uuid4())
                        deleted = await probe.request("delete owned fixture", "DELETE", path,
                            tokens[0], operation=operation, revision=revision,
                            body={"operation_id": operation, "expected_revision": revision})
                        await probe.request("deleted document hidden", "GET", path,
                                            tokens[0], status=404)
                        retained = await probe.request("receipt after deletion", "GET",
                            f"/api/v1/viz/operations/{operation}", tokens[0])
                        probe.check("deleted receipt retained", retained.json() == deleted.json())
                        report["cleanup"] = "SOFT_DELETED"
                    elif current.status_code == 404 and not created:
                        report["cleanup"] = "NOT_CREATED"
                    else:
                        report["cleanup"] = "UNVERIFIED"
                        report["result"] = "FAIL"
                except Exception as error:
                    report.update(cleanup="UNVERIFIED", result="FAIL",
                                  cleanup_error_type=type(error).__name__)
            for token in tokens:
                try:
                    await probe.request("logout QA session", "POST", "/api/v1/auth/logout", token)
                except Exception as error:
                    report.update(result="FAIL", logout_error_type=type(error).__name__)
            report["steps"] = probe.steps
            report["finished_at"] = datetime.now(UTC).isoformat()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--owner", required=True)
    parser.add_argument("--outsider", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.owner == args.outsider:
        parser.error("Two distinct QA identities are required")
    report = asyncio.run(run(args))
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"result": report["result"], "cleanup": report["cleanup"]}))
    raise SystemExit(0 if report["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
