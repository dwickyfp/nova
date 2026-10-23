"""Restartable metadata-only migration; dashboard SQL is never executed here."""

from __future__ import annotations

import json

from pydantic import ValidationError

from app.core.database import db
from app.modules.intelligence.engine_repository import metadata_lock
from app.modules.smart_reports.repository import digest, report_repository
from app.modules.smart_reports.schemas import Page, Placement, ReportSpecV1, Section, Widget
from app.modules.smart_reports.service import now, stable_id


def replacement_spec(title: str, raw: object) -> tuple[ReportSpecV1, list[str]]:
    issues: list[str] = []
    try:
        layout = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        layout = None
    if not isinstance(layout, dict) or not isinstance(layout.get("tiles"), list):
        layout = {"tiles": [{}]}
        issues.append("invalid_layout")
    tiles = layout["tiles"]
    overflow = len(tiles) > 240
    if overflow:
        issues.append(f"capacity_exceeded:{len(tiles)}_tiles_source_retained")
        tiles = tiles[:239]
    # Legacy format is 6 x 4. Invalid/overlapping positions retain a placeholder
    # in reading order instead of preventing the remaining dashboards migrating.
    widgets = []
    occupied: set[tuple[int, int]] = set()
    for index, tile in enumerate(tiles):
        if index % 24 == 0:
            occupied.clear()
        tile = tile if isinstance(tile, dict) else {}
        try:
            if any(type(tile.get(key)) is not int for key in ("x", "y", "w", "h")) or not (
                0 <= tile["x"] < 6
                and 0 <= tile["y"] < 4
                and 1 <= tile["w"] <= 6 - tile["x"]
                and 1 <= tile["h"] <= 4 - tile["y"]
            ):
                raise ValueError("Invalid legacy grid coordinates")
            placement = Placement(
                col=tile["x"] * 2, row=tile["y"], width=tile["w"] * 2, height=tile["h"]
            )
            cells = {
                (x, y)
                for x in range(placement.col, placement.col + placement.width)
                for y in range(placement.row, placement.row + placement.height)
            }
            if cells & occupied:
                raise ValueError("overlap")
        except (ValidationError, KeyError, ValueError, TypeError):
            placement = Placement(
                col=0, row=max((y for _x, y in occupied), default=-1) + 1, width=12, height=2
            )
            cells = {(x, y) for x in range(12) for y in range(placement.row, placement.row + 2)}
            issues.append(f"tile_{index}:invalid_position")
        occupied |= cells
        artifact = tile.get("artifact_id")
        if not isinstance(artifact, str) or not artifact or len(artifact) > 64:
            artifact = None
            issues.append(f"tile_{index}:missing_artifact")
        try:
            widget = Widget(
                id=f"tile_{index}",
                title=f"Artifact {index + 1}",
                mapping_required=True,
                legacy_artifact_id=artifact,
                placement=placement,
            )
        except ValidationError:
            widget = Widget(
                id=f"tile_{index}",
                title=f"Unmapped tile {index + 1}",
                mapping_required=True,
                placement=placement,
            )
            issues.append(f"tile_{index}:invalid_artifact")
        widgets.append(widget)
    if overflow:
        widgets += [
            Widget(
                id="overflow",
                title="Additional tiles require mapping from the legacy dashboard",
                mapping_required=True,
                placement=Placement(col=0, row=0, width=12, height=2),
            )
        ]
        widgets[-1].placement.row = max(
            w.placement.row + w.placement.height for w in widgets[216:-1]
        )
    pages = [
        Page(
            id=f"page_{start // 24}",
            title="Overview" if start == 0 else f"Page {start // 24 + 1}",
            sections=[Section(id=f"section_{start // 24}", widgets=widgets[start : start + 24])],
        )
        for start in range(0, max(1, len(widgets)), 24)
    ]
    return ReportSpecV1(title=(title or "Untitled dashboard")[:256], pages=pages), issues


async def source_spec(owner: str, title: str, raw: object) -> tuple[ReportSpecV1, list[str]]:
    spec, issues = replacement_spec(title, raw)
    for widget in spec.widgets().values():
        if widget.legacy_artifact_id:
            artifact = await db.execute_system(
                "SELECT title FROM NOVA_SYSTEM.CONFIG_STUDIO_ARTIFACTS "
                "WHERE artifact_id=%s AND owner_name=%s",
                [widget.legacy_artifact_id, owner],
            )
            if artifact["rows"]:
                widget.title = str(artifact["rows"][0][0])[:256]
            else:
                issues.append(f"{widget.id}:missing_artifact")
    return spec, issues


async def migrate_dashboards() -> dict[str, int]:
    after = ""
    scanned = migrated = changed = 0
    while True:
        batch = await db.execute_system(
            "SELECT dashboard_id,owner_name,title,layout_json,updated_at "
            "FROM NOVA_SYSTEM.CONFIG_STUDIO_DASHBOARDS "
            "WHERE dashboard_id>%s ORDER BY dashboard_id LIMIT 100",
            [after],
        )
        if not batch["rows"]:
            break
        for dashboard_id, owner, title, raw, updated_at in batch["rows"]:
            scanned += 1
            after = dashboard_id
            report_id = stable_id("legacy-dashboard", dashboard_id)
            draft_id = stable_id(report_id, "draft", owner)
            source_hash = digest([title, raw, str(updated_at)])
            operation_id = stable_id("migrate-dashboard", dashboard_id, source_hash)
            ledger_id = stable_id("dashboard-migration", dashboard_id)
            request = {"dashboard_id": dashboard_id, "source_hash": source_hash}
            async with metadata_lock(f"smart-report:{report_id}") as lease:
                existing = await report_repository.read(report_id)
                draft = await report_repository.read(draft_id)
                ledger = await report_repository.read(ledger_id)
                if existing and draft:
                    status = (
                        "source_changed"
                        if existing["payload"]["legacy_source_hash"] != source_hash
                        else "migrated"
                    )
                    if ledger:
                        issues = ledger["payload"].get("issues", [])
                    else:
                        _source, issues = await source_spec(owner, title, raw)
                    if status == "source_changed":
                        changed += 1
                else:
                    spec, issues = await source_spec(owner, title, raw)
                    if not existing:
                        await report_repository.append(
                            record_id=report_id,
                            report_id=report_id,
                            kind="report",
                            owner=owner,
                            operation_id=operation_id,
                            expected_revision=0,
                            request=request,
                            lease=lease,
                            payload={
                                "kind": "report",
                                "report_id": report_id,
                                "owner": owner,
                                "title": spec.title,
                                "published_revision": 0,
                                "archived": False,
                                "legacy_dashboard_id": dashboard_id,
                                "legacy_source_hash": source_hash,
                                "created_at": now(),
                                "updated_at": now(),
                            },
                        )
                    await report_repository.append(
                        record_id=draft_id,
                        report_id=report_id,
                        kind="draft",
                        owner=owner,
                        operation_id=operation_id,
                        expected_revision=0,
                        request=request,
                        lease=lease,
                        payload={
                            "report_id": report_id,
                            "editor": owner,
                            "base_published_revision": 0,
                            "spec": spec.model_dump(mode="json"),
                            "updated_at": now(),
                        },
                    )
                    status = (
                        "source_changed"
                        if existing and existing["payload"]["legacy_source_hash"] != source_hash
                        else "migrated"
                    )
                    migrated += 1
                if ledger and all(
                    ledger["payload"].get(key) == value
                    for key, value in {**request, "status": status}.items()
                ):
                    continue
                await report_repository.append(
                    record_id=ledger_id,
                    report_id=report_id,
                    kind="migration",
                    owner=owner,
                    operation_id=stable_id(
                        "migration-check",
                        dashboard_id,
                        source_hash,
                        str(ledger["revision"] if ledger else 0),
                    ),
                    expected_revision=ledger["revision"] if ledger else 0,
                    request=request,
                    lease=lease,
                    payload={
                        **request,
                        "report_id": report_id,
                        "draft_id": draft_id,
                        "status": status,
                        "issues": issues,
                        "checked_at": now(),
                    },
                )
    return {"scanned": scanned, "migrated": migrated, "source_changed": changed}
