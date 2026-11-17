"""Inspect governed QA sales and create/delete only a new personal native dataset.

Use the existing QA identities; passwords arrive through environment variables.
No analytical data, grants, policies or deployment state are changed.
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


async def verify_dashboard_registration(
    probe: LiveCheck, owner: str, dataset_id: str, selection: dict[str, Any],
) -> None:
    dashboard_id, page_id = str(uuid4()), str(uuid4())
    path = f"/api/v1/viz/dashboards/{dashboard_id}"
    new_dataset_id = str(uuid4())
    child_path = f"/api/v1/viz/datasets/{new_dataset_id}"
    try:
        await probe.request(
            "create owned dataset dashboard", "POST", "/api/v1/viz/dashboards", owner,
            operation=str(uuid4()), status=201, body={
                "workspace_id": "personal", "definition": {
                    "dashboard_id": dashboard_id, "title": "Dataset registry acceptance",
                    "pages": [{"page_id": page_id, "title": "Overview"}],
                },
            },
        )
        key = str(uuid4())
        command = {"operation_id": key, "expected_revision": 1, "changes": [
            {"op": "datasets.replace", "datasets": [{"dataset_id": dataset_id, "revision": 1}]},
        ]}
        attached = await probe.request(
            "attach dashboard dataset", "POST", path + "/operations", owner,
            operation=key, revision=1, body=command,
        )
        replay = await probe.request(
            "replay dataset attachment", "POST", path + "/operations", owner,
            operation=key, revision=1, body=command,
        )
        probe.check("dataset attachment receipt stable", replay.json() == attached.json())
        read = await probe.request("read attached dataset", "GET", path + "?draft=true", owner)
        probe.check("dashboard pins dataset without creating widgets",
                    read.json()["revision"] == 2
                    and read.json()["definition"]["datasets"] == [
                        {"dataset_id": dataset_id, "revision": 1},
                    ] and read.json()["definition"]["pages"][0]["widgets"] == [])
        key = str(uuid4())
        await probe.request(
            "dashboard protects registered dataset", "DELETE", f"/api/v1/viz/datasets/{dataset_id}",
            owner, operation=key, revision=1, status=409,
            body={"operation_id": key, "expected_revision": 1},
        )
        inspected = await probe.request(
            "inspect new dashboard dataset", "POST", "/api/v1/viz/datasets/inspect", owner,
            body={"dataset_id": new_dataset_id, "source": selection},
        )
        definition = {"dataset_id": new_dataset_id, "source": selection, "title": "Attached data",
                      "expected_schema_fingerprint": inspected.json()["schema_fingerprint"]}
        if selection["kind"] in {"table", "view"}:
            definition["expected_physical_id"] = inspected.json()["source"]["physical_id"]
        key = str(uuid4())
        created = await probe.request(
            "create dataset in dashboard atomically", "POST", path + "/datasets", owner,
            operation=key, revision=2, status=201, body={"dataset": definition},
        )
        replay = await probe.request(
            "replay atomic dataset creation", "POST", path + "/datasets", owner,
            operation=key, revision=2, status=201, body={"dataset": definition},
        )
        probe.check("atomic dataset creation receipt stable", replay.json() == created.json())
        saved = await probe.request("read newly attached dataset", "GET",
                                    child_path + "?draft=true", owner)
        probe.check("atomic dataset creation retains server fields",
                    saved.json()["definition"]["fields"] == inspected.json()["fields"])
        board = await probe.request("read dashboard after dataset creation", "GET",
                                    path + "?draft=true", owner)
        probe.check("atomic dataset creation appends one reference",
                    board.json()["revision"] == 3 and board.json()["definition"]["datasets"] == [
                        {"dataset_id": dataset_id, "revision": 1},
                        {"dataset_id": new_dataset_id, "revision": 1},
                    ])
        index = await probe.request("read pinned dashboard dataset index", "GET",
                                    path + "/datasets?revision=2", owner)
        probe.check("dataset index retains old dashboard references and titles", index.json() == {
            "dashboard_id": dashboard_id, "revision": 2, "datasets": [
                {"dataset_id": dataset_id, "revision": 1, "title": "Native dataset acceptance"},
            ],
        })
        await probe.request(
            "edit attached dataset independently", "PATCH", child_path + "/draft", owner,
            operation=str(uuid4()), revision=1, body={**definition, "title": "Attached revised"},
        )
        current = await probe.request("read current dashboard dataset index", "GET",
                                      path + "/datasets?revision=3", owner)
        probe.check("dataset index appends only confirmed creation",
                    current.json()["datasets"][1] == {
                        "dataset_id": new_dataset_id, "revision": 1, "title": "Attached data",
                    })
        pinned_preview = await probe.request(
            "preview pinned dataset after later draft edit", "POST", child_path + "/preview",
            owner, body={"revision": 1, "draft": True, "limit": 1},
        )
        probe.check("preview retains pinned fields and revision",
                    pinned_preview.json()["revision"] == 1
                    and pinned_preview.json()["fields"] == inspected.json()["fields"])
        original = await probe.request("read immutable dashboard revision", "GET",
                                       path + "?draft=true&revision=1", owner)
        probe.check("historical read does not substitute latest draft",
                    original.json()["revision"] == 1 and original.headers["etag"] == '"1"'
                    and not original.json()["definition"].get("datasets"))
        # Since a324eeac a revision read needs no draft flag: row security
        # admits an editor to every revision and a viewer only to a revision a
        # visible publication pins. The editor gets the same revision 1.
        pinned = await probe.request("editor reads a historical revision", "GET",
                                     path + "?revision=1", owner)
        probe.check("historical revision is exact", pinned.json()["revision"] == 1
                    and pinned.json()["definition"] == original.json()["definition"])
    finally:
        for label, owned in (("dataset dashboard", path), ("dashboard dataset", child_path)):
            read = await probe.client.get(
                owned + "?draft=true", headers={"Authorization": f"Bearer {owner}"},
            )
            if read.status_code == 200:
                key, revision = str(uuid4()), read.json()["revision"]
                await probe.request(
                    f"delete owned {label}", "DELETE", owned, owner,
                    operation=key, revision=revision,
                    body={"operation_id": key, "expected_revision": revision},
                )
            elif read.status_code != 404:
                raise AssertionError(f"Owned {label} cleanup could not be verified")


async def run(args: argparse.Namespace) -> dict[str, Any]:
    identity = str(uuid4())
    path = f"/api/v1/viz/datasets/{identity}"
    selection = {
        "kind": args.kind,
        "database": args.database,
        "object_name": args.object,
        "columns": args.columns,
    }
    if getattr(args, "sql", None):
        selection = {"kind": "sql", "database": args.database, "sql": args.sql}
    if getattr(args, "semantic", None):
        selection = args.semantic
    report: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "resource_id": identity,
        "scope": f"{selection['kind']} dataset API, real StarRocks/Ranger and PG17 metadata",
        "result": "FAIL",
        "cleanup": "NOT_RUN",
    }
    tokens: list[str] = []
    async with httpx.AsyncClient(base_url=args.base_url, timeout=40) as client:
        probe = LiveCheck(client)
        try:
            for username, variable in (
                (args.owner, "NOVA_QA_OWNER_PASSWORD"),
                (args.outsider, "NOVA_QA_OUTSIDER_PASSWORD"),
            ):
                response = await client.post(
                    "/api/v1/auth/login",
                    json={
                        "username": username,
                        "password": os.environ[variable],
                    },
                )
                probe.check("QA login", response.status_code == 200)
                login = response.json()
                probe.check("QA admitted", login["status"] == "AUTHENTICATED")
                tokens.append(login["access_token"])
                probe.check("non-admin role", login["active_role"] != "ACCOUNTADMIN")
            owner, outsider = tokens
            if args.role:
                refused = await probe.request(
                    "inactive fixture role cannot inspect source",
                    "POST",
                    "/api/v1/viz/datasets/inspect",
                    owner,
                    status=404 if selection["kind"] == "semantic_model" else 403,
                    body={"dataset_id": identity, "source": selection},
                )
                probe.check("source refusal has no private name", args.object not in refused.text)
                denied_create = {
                    "dataset_id": identity,
                    "source": selection,
                    "workspace_id": "personal",
                    "title": "Refused source fixture",
                    "expected_schema_fingerprint": "0" * 64,
                }
                if selection["kind"] in {"table", "view"}:
                    denied_create["expected_physical_id"] = "1"
                refused = await probe.request(
                    "inactive role cannot create source dataset",
                    "POST",
                    "/api/v1/viz/datasets",
                    owner,
                    operation=str(uuid4()),
                    status=404 if selection["kind"] == "semantic_model" else 403,
                    body=denied_create,
                )
                probe.check("create refusal has no private name", args.object not in refused.text)
                switched = await probe.request(
                    "activate fixture role",
                    "POST",
                    "/api/v1/auth/switch-role",
                    owner,
                    body={"role": args.role},
                )
                probe.check("exact active role", switched.json()["active_role"] == args.role)
                await probe.request(
                    "denied create left no dataset", "GET", path + "?draft=true", owner, status=404
                )
            inspected = await probe.request(
                "inspect",
                "POST",
                "/api/v1/viz/datasets/inspect",
                owner,
                body={"dataset_id": identity, "source": selection},
            )
            schema = inspected.json()
            probe.check(
                "schema without data",
                "rows" not in schema
                and "sample" not in schema
                and [f["source_column"] for f in schema["fields"]] == args.columns,
            )
            sources = (
                schema["source"]["dependencies"]
                if selection["kind"] in {"sql", "semantic_model"}
                else [schema["source"]]
            )
            probe.check(
                "physical identity",
                bool(sources)
                and all(
                    source["physical_id"].isdigit() and int(source["physical_id"]) > 0
                    for source in sources
                ),
            )
            # NDV-DATA-006: the dataset names its source exactly as the engine
            # does (kind, catalog, database, object) and a SQL dataset lists the
            # view it reads as a dependency, never the view's base table.
            if selection["kind"] in {"view", "table", "sql"}:
                lineage = [source for source in sources
                           if source.get("object_name") == args.object]
                probe.check(
                    "source lineage and qualified name",
                    len(lineage) == 1
                    and lineage[0]["kind"] == args.kind
                    and lineage[0]["catalog"] == "default_catalog"
                    and lineage[0]["database"] == args.database
                    and lineage[0].get("schema_name") in (None, "default"),
                    sources=[{key: source.get(key) for key in
                              ("kind", "catalog", "database", "schema_name", "object_name")}
                             for source in sources],
                )
            expected_types = getattr(args, "expected_types", None)
            if expected_types:
                observed_types = {f["source_column"]: [f["logical_type"], f["physical_type"]]
                                  for f in schema["fields"]}
                probe.check("source field types", all(
                    observed_types.get(column, [None, None])[0] == logical
                    and str(observed_types[column][1]).lower().startswith(physical)
                    for column, (logical, physical) in expected_types.items()),
                    observed=observed_types)
            if selection["kind"] == "sql":
                probe.check(
                    "parsed query digest",
                    len(schema["source"]["ast_digest"]) == 64
                    and schema["source"]["sql"] == args.sql,
                )
            probe.check(
                "authoritative fields",
                all(
                    isinstance(f["nullable"], bool) and f["physical_type"] and f["field_id"]
                    for f in schema["fields"]
                ),
            )
            probe.check("no browser cache", inspected.headers["cache-control"] == "no-store")
            body = {
                "dataset_id": identity,
                "source": selection,
                "workspace_id": "personal",
                "title": "Native dataset acceptance",
                "expected_schema_fingerprint": schema["schema_fingerprint"],
            }
            if selection["kind"] in {"table", "view"}:
                body["expected_physical_id"] = schema["source"]["physical_id"]
            await probe.request(
                "stale binding refused",
                "POST",
                "/api/v1/viz/datasets",
                owner,
                operation=str(uuid4()),
                status=409,
                body={**body, "expected_schema_fingerprint": "0" * 64},
            )
            await probe.request(
                "spoofed schema refused",
                "POST",
                "/api/v1/viz/datasets",
                owner,
                operation=str(uuid4()),
                status=422,
                body={**body, "fields": schema["fields"]},
            )
            create_key = key = str(uuid4())
            created = await probe.request(
                "create",
                "POST",
                "/api/v1/viz/datasets",
                owner,
                operation=key,
                status=201,
                body=body,
            )
            replay = await probe.request(
                "replay",
                "POST",
                "/api/v1/viz/datasets",
                owner,
                operation=key,
                status=201,
                body=body,
            )
            probe.check("same receipt", created.json() == replay.json())
            await verify_dashboard_registration(probe, owner, identity, selection)
            draft = await probe.request("draft", "GET", path + "?draft=true", owner)
            probe.check(
                "stored server schema",
                draft.json()["definition"]["fields"] == schema["fields"]
                and draft.json()["definition"]["source"] == schema["source"],
            )
            await probe.request(
                "personal isolation", "GET", path + "?draft=true", outsider, status=404
            )
            preview = await probe.request(
                "preview draft",
                "POST",
                path + "/preview",
                owner,
                body={"revision": 1, "limit": 1},
            )
            page = preview.json()
            probe.check(
                "bounded typed preview without fabricated total",
                len(page["rows"]) <= 1
                and page["fields"] == schema["fields"]
                and "total" not in page
                and "sql" not in page,
            )
            probe.check("preview cache disabled", preview.headers["cache-control"] == "no-store")
            expected_rows = getattr(args, "preview_rows", None)
            if expected_rows is not None:
                observed = list(page["rows"])
                while page["next_offset"] is not None:
                    next_page = await probe.request(
                        "preview next page",
                        "POST",
                        path + "/preview",
                        owner,
                        body={"revision": 1, "limit": 1, "offset": page["next_offset"]},
                    )
                    page = next_page.json()
                    observed.extend(page["rows"])
                    probe.check("pagination bounded", len(observed) <= len(expected_rows))
                report["preview_field_types"] = [
                    {
                        key: field[key]
                        for key in ("source_column", "logical_type", "physical_type", "nullable")
                    }
                    for field in schema["fields"]
                ]
                report["preview_mismatches"] = [
                    {
                        "row": row_index,
                        "column": column_index,
                        "actual_type": actual.get("type") if isinstance(actual, dict) else None,
                        "expected_type": expected.get("type")
                        if isinstance(expected, dict)
                        else None,
                    }
                    for row_index, (actual_row, expected_row) in enumerate(
                        zip(observed, expected_rows, strict=False)
                    )
                    for column_index, (actual, expected) in enumerate(
                        zip(actual_row, expected_row, strict=False)
                    )
                    if actual != expected
                ]
                probe.check("exact typed preview pages", observed == expected_rows)
            await probe.request(
                "preview isolation",
                "POST",
                path + "/preview",
                outsider,
                status=404,
                body={"revision": 1},
            )
            await probe.request(
                "preview missing revision",
                "POST",
                path + "/preview",
                owner,
                status=404,
                body={"revision": 2},
            )
            await probe.request(
                "preview bounded input",
                "POST",
                path + "/preview",
                owner,
                status=422,
                body={"revision": 1, "limit": 201},
            )
            key = str(uuid4())
            await probe.request(
                "publish",
                "POST",
                path + "/publish",
                owner,
                operation=key,
                revision=1,
                body={"operation_id": key, "expected_revision": 1},
            )
            published = await probe.request("read publication", "GET", path, owner)
            publication = published.json()["definition"]
            probe.check(
                "publication retains public schema without source definitions",
                publication["fields"] == draft.json()["definition"]["fields"]
                and publication["title"] == draft.json()["definition"]["title"]
                and publication["source_kind"] == selection["kind"]
                and publication["definition_scope"] == "publication"
                and "source" not in publication and "schema_fingerprint" not in publication
                and all("formula" not in metric for metric in publication["metrics"]),
            )
            await probe.request("publication isolation", "GET", path, outsider, status=404)
            await probe.request(
                "preview publication",
                "POST",
                path + "/preview",
                owner,
                body={"revision": 1, "draft": False, "limit": 1},
            )
            after_publish = getattr(args, "after_dataset_publish", None)
            history_marker = getattr(args, "history_marker", None)
            if history_marker:
                history = await probe.request(
                    "dataset query history", "GET",
                    f"/api/v1/query/history?limit=500&database_name={args.database}", owner,
                )
                history_items = history.json()["items"]
                probe.check("dataset execution retains history", bool(history_items))
                dataset_events = [item for item in history_items if item["object_name"] == identity]
                probe.check(
                    "dataset history retains actor, resource and revision",
                    any(item["user_name"] == args.owner and item["status"] == "SUCCESS"
                        and item["sql_text"] == f"Dataset {identity} revision 1: preview"
                        and item["query_id"] for item in dataset_events),
                )
                private_events = history_items if selection["kind"] == "sql" else dataset_events
                probe.check(
                    "dataset history omits generated source SQL",
                    all(history_marker.casefold() not in (item.get("sql_text") or "").casefold()
                        for item in private_events),
                )
            if after_publish is not None:
                await after_publish(probe, owner, path)
            repair = getattr(args, "repair_source", None)
            if repair is not None:
                await repair()
                recovered = await probe.request(
                    "create replay after source replacement", "POST", "/api/v1/viz/datasets",
                    owner, operation=create_key, status=201, body=body,
                )
                probe.check("source replacement preserves create receipt",
                            recovered.json() == created.json())
                await probe.request(
                    "changed create intent refused after replacement", "POST",
                    "/api/v1/viz/datasets", owner, operation=create_key, status=409,
                    body={**body, "title": "Different intent"},
                )
                await probe.request(
                    "replaced source refuses published preview", "POST", path + "/preview",
                    owner, status=409, body={"revision": 1, "draft": False},
                )
            refreshed = await probe.request(
                "inspect edited source", "POST", "/api/v1/viz/datasets/inspect", owner,
                body={"dataset_id": identity, "source": selection},
            )
            edited_schema = refreshed.json()
            edit = {
                "dataset_id": identity, "source": selection, "title": "Reviewed dataset source",
                "expected_schema_fingerprint": edited_schema["schema_fingerprint"],
            }
            if selection["kind"] in {"table", "view"}:
                edit["expected_physical_id"] = edited_schema["source"]["physical_id"]
            if repair is not None:
                probe.check(
                    "replacement has a distinct physical identity",
                    schema["source"]["physical_id"] != edited_schema["source"]["physical_id"],
                )
                probe.check(
                    "replacement does not silently reuse old field bindings",
                    {f["field_id"] for f in schema["fields"]}.isdisjoint(
                        f["field_id"] for f in edited_schema["fields"]
                    ),
                )
            await probe.request(
                "source edit isolation", "PATCH", path + "/draft", outsider,
                operation=str(uuid4()), revision=1, body=edit, status=404,
            )
            edit_key = str(uuid4())
            changed = await probe.request(
                "explicit source edit", "PATCH", path + "/draft", owner,
                operation=edit_key, revision=1, body=edit,
            )
            replay = await probe.request(
                "source edit replay", "PATCH", path + "/draft", owner,
                operation=edit_key, revision=1, body=edit,
            )
            probe.check("source edit receipt is stable", changed.json() == replay.json())
            await probe.request(
                "source edit stale revision", "PATCH", path + "/draft", owner,
                operation=str(uuid4()), revision=1, body=edit, status=409,
            )
            await probe.request(
                "source edit changed idempotent intent", "PATCH", path + "/draft", owner,
                operation=edit_key, revision=1,
                body={**edit, "title": "Another intent"}, status=409,
            )
            current = await probe.request("edited draft", "GET", path + "?draft=true", owner)
            probe.check(
                "source edit creates a second server-derived revision",
                current.json()["revision"] == 2
                and current.json()["definition"]["fields"] == edited_schema["fields"]
                and current.json()["definition"]["source"] == edited_schema["source"],
            )
            original = await probe.request("publication after edit", "GET", path, owner)
            probe.check(
                "source edit preserves original publication",
                original.json()["revision"] == 1
                and original.json()["definition"] == published.json()["definition"],
            )
            await probe.request(
                "edited source preview", "POST", path + "/preview", owner,
                body={"revision": 2, "limit": 1},
            )
            if repair is not None:
                await repair()
                recovered = await probe.request(
                    "edit replay after source replacement", "PATCH", path + "/draft", owner,
                    operation=edit_key, revision=1, body=edit,
                )
                probe.check("source replacement preserves edit receipt",
                            recovered.json() == changed.json())
                await probe.request(
                    "new edit refuses stale source", "PATCH", path + "/draft", owner,
                    operation=str(uuid4()), revision=2, body=edit, status=409,
                )
                unchanged = await probe.request("replay keeps revision", "GET",
                                                path + "?draft=true", owner)
                probe.check("replay adds no revision", unchanged.json()["revision"] == 2)
            report["result"] = "PASS"
        except Exception as error:
            report["error_type"] = type(error).__name__
        finally:
            if tokens:
                try:
                    draft = await client.get(
                        path + "?draft=true",
                        headers={
                            "Authorization": f"Bearer {tokens[0]}",
                        },
                    )
                    if draft.status_code == 200:
                        key = str(uuid4())
                        revision = draft.json()["revision"]
                        await probe.request(
                            "delete owned dataset",
                            "DELETE",
                            path,
                            tokens[0],
                            operation=key,
                            revision=revision,
                            body={"operation_id": key, "expected_revision": revision},
                        )
                        await probe.request(
                            "deleted dataset hidden", "GET", path, tokens[0], status=404
                        )
                        report["cleanup"] = "SOFT_DELETED"
                    elif draft.status_code == 404:
                        report["cleanup"] = "NOT_CREATED"
                    else:
                        raise AssertionError("Cleanup observation failed")
                except Exception as error:
                    report.update(
                        cleanup="UNVERIFIED", result="FAIL", cleanup_error_type=type(error).__name__
                    )
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
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--owner", required=True)
    parser.add_argument("--outsider", required=True)
    parser.add_argument("--role")
    parser.add_argument("--database", default="analytics")
    parser.add_argument("--object", default="sales")
    parser.add_argument("--kind", choices=("table", "view"), default="table")
    parser.add_argument("--sql", help="A read-only query against the owned governed fixture")
    parser.add_argument("--columns", nargs="+", default=["id", "city", "amount"])
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
