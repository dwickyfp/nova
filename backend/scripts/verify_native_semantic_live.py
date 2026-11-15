"""Exercise a new Semantic View and native dataset on an owned source fixture."""

from __future__ import annotations

import argparse
import json
import os
from typing import Any
from uuid import uuid4

import httpx

from scripts.verify_native_datasets_live import run as verify_dataset
from scripts.verify_native_documents_live import LiveCheck


async def run(args: argparse.Namespace) -> dict[str, Any]:
    report: dict[str, Any] = {"result": "FAIL", "cleanup": "NOT_CREATED"}
    token = ""
    view_id = ""
    name = "ndv_semantic_" + uuid4().hex[:12]

    def definition(multiplier: int) -> str:
        def expression(sql: str) -> dict:
            return {"dialects": [{"dialect": "ANSI_SQL", "expression": sql}]}
        return json.dumps({
            "version": "0.1.1", "name": name,
            "datasets": [{"name": "orders", "source": f"{args.database}.source_table",
                          "primary_key": ["id"], "fields": [
                              {"name": "id", "expression": expression("id"),
                               "kind": "dimension"},
                              {"name": "amount", "expression": expression("amount"),
                               "datatype": "Decimal"},
                          ]}],
            "metrics": [{"name": "total", "datatype": "Decimal",
                         "expression": expression(f"SUM(orders.amount) * {multiplier}")}],
        })

    async with httpx.AsyncClient(base_url=args.base_url, timeout=90) as client:
        probe = LiveCheck(client)
        try:
            login = await client.post("/api/v1/auth/login", json={
                "username": args.owner, "password": os.environ["NOVA_QA_OWNER_PASSWORD"],
            })
            probe.check("semantic fixture login", login.status_code == 200)
            token = login.json()["access_token"]
            await probe.request("semantic fixture active role", "POST", "/api/v1/auth/switch-role",
                                token, body={"role": args.role})
            created = await probe.request("create owned semantic view", "POST",
                                          "/api/v1/semantic-views", token, status=201,
                                          body={"name": name, "database": args.database,
                                                "definition": definition(1)})
            view_id = created.json()["id"]
            path = f"/api/v1/semantic-views/{view_id}"
            fingerprint = created.json()["versions"][0]["fingerprint"]
            selection = dict(kind="semantic_model", model_id=view_id, model_revision=1,
                             model_fingerprint=fingerprint, dimensions=["id"], metrics=["total"])
            inspect_body = {"dataset_id": str(uuid4()), "source": selection}
            await probe.request("draft model cannot author dataset", "POST",
                                "/api/v1/viz/datasets/inspect", token,
                                status=404, body=inspect_body)
            checked = await probe.request("validate semantic model", "POST",
                                          path + "/versions/1/validate", token)
            probe.check("semantic validation passed", checked.json()["valid"] is True)
            published = await probe.request("publish semantic model", "POST",
                                            path + "/versions/1/publish", token)
            probe.check("published version one", published.json()["active_version"] == 1)
            await probe.request("incorrect semantic fingerprint refused", "POST",
                                "/api/v1/viz/datasets/inspect", token, status=409,
                                body={**inspect_body, "source": {**selection,
                                                               "model_fingerprint": "0" * 64}})

            async def advance_model(dataset_probe: LiveCheck, caller: str, dataset_path: str):
                await probe.request("new semantic version", "POST", path + "/versions", token,
                                    status=201, body={"definition": definition(2)})
                validated = await probe.request("validate new semantic version", "POST",
                                                 path + "/versions/2/validate", token)
                probe.check("new semantic version valid", validated.json()["valid"] is True)
                latest = await probe.request("publish new semantic version", "POST",
                                              path + "/versions/2/publish", token)
                probe.check("new active semantic version", latest.json()["active_version"] == 2)
                old = await dataset_probe.request("native dataset keeps pinned semantic version",
                                                   "POST", dataset_path + "/preview", caller,
                                                   body={"revision": 1, "draft": False})
                probe.check("pinned semantic result unchanged",
                            old.json()["rows"] == args.preview_rows)

            result = await verify_dataset(argparse.Namespace(
                owner=args.owner, outsider=args.outsider, role=args.role, database=args.database,
                object="source_table", kind="table", columns=["id", "total"], semantic=selection,
                preview_rows=args.preview_rows, base_url=args.base_url,
                after_dataset_publish=advance_model,
                history_marker="sum(",
            ))
            report["dataset"] = result
            probe.check("native semantic dataset lifecycle", result["result"] == "PASS")
            await probe.request("deprecate owned semantic view", "POST", path + "/deprecate",
                                token, status=204)
            await probe.request("deprecated model refuses new dataset inspection", "POST",
                                "/api/v1/viz/datasets/inspect", token,
                                status=404, body=inspect_body)
            report["result"] = "PASS"
        except Exception as error:
            report["error_type"] = type(error).__name__
        finally:
            if view_id:
                try:
                    await probe.request("remove owned semantic view", "DELETE",
                                        f"/api/v1/semantic-views/{view_id}", token, status=204)
                    await probe.request("removed semantic view hidden", "GET",
                                        f"/api/v1/semantic-views/{view_id}", token, status=404)
                    report["cleanup"] = "REMOVED"
                except Exception as error:
                    report.update(result="FAIL", cleanup="UNVERIFIED",
                                  cleanup_error_type=type(error).__name__)
            if token:
                try:
                    await probe.request("semantic fixture logout", "POST",
                                        "/api/v1/auth/logout", token)
                except Exception as error:
                    report.update(result="FAIL", logout_error_type=type(error).__name__)
            report["steps"] = probe.steps
    return report
