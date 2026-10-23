"""Verified artifact provenance and copy-to-draft operations."""

import json
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError

from app.modules.agents.artifact_repository import artifact_repository, validate_artifact_sql
from app.modules.intelligence.engine_repository import metadata_lock
from app.modules.smart_reports.repository import digest, report_repository
from app.modules.smart_reports.runtime import report_runtime
from app.modules.smart_reports.schemas import (
    Encoding,
    Page,
    PatchRequest,
    Placement,
    QuerySpec,
    ReportPatch,
    ReportSpecV1,
    Section,
    SemanticSource,
    Widget,
)
from app.modules.smart_reports.service import report_service, stable_id
from app.modules.smart_reports.sessions import redis_client, scope


def artifact_fingerprint(artifact: dict) -> str:
    return digest([artifact["sql_text"], artifact.get("chart_spec"), artifact["artifact_type"]])


def artifact_visual(artifact: dict, query: QuerySpec) -> dict | None:
    if artifact["artifact_type"] == "table":
        return {"kind": "table", "encoding": Encoding()}
    chart = artifact.get("chart_spec") or {}
    encoding = chart.get("encoding")
    mark = chart.get("mark")
    if (
        not isinstance(mark, str)
        or mark not in {"bar", "line", "area", "point", "circle", "arc"}
        or not isinstance(encoding, dict)
    ):
        return None
    if set(encoding) - {"x", "y", "color", "theta", "tooltip"}:
        return None
    selected = [*query.dimensions, *query.metrics, *(item.name for item in query.calculations)]
    if query.time and query.time.grain:
        selected.append("period")

    def field(channel: str) -> str | None:
        definition = encoding.get(channel, {})
        if not isinstance(definition, dict) or set(definition) - {"field", "type", "title"}:
            return None
        matches = [
            name for name in selected if definition.get("field") in {name, name.replace(".", "__")}
        ]
        return matches[0] if len(matches) == 1 else None

    x, y, series = field("x"), field("y"), field("color")
    if mark == "arc":
        x, y, series = series, field("theta"), None
    if not x or not y or "color" in encoding and mark != "arc" and not series:
        return None
    if set(query.dimensions) - {x, series}:
        return None
    if y not in {*query.metrics, *(item.name for item in query.calculations)}:
        return None
    if series and series not in query.dimensions:
        return None
    kind = {"point": "scatter", "circle": "scatter", "arc": "donut"}.get(mark, mark)
    if mark == "bar" and series:
        kind = "stacked_bar"
    return {"kind": kind, "encoding": Encoding(x=x, y=[y], series=series)}


async def issue_provenance(result: dict, user: dict) -> str | None:
    if not all(
        result.get(key) is not None
        for key in ("plan", "view_id", "version", "model_fingerprint", "sql")
    ):
        return None
    plan = result["plan"]
    if plan.get("fiscal_start_month", 1) != 1 or plan.get("week_start", 0) != 0:
        return None
    # Only lossless conversions get a semantic binding.
    if any(plan.get(key) for key in ("having", "top_n_per_group", "unresolved_concepts", "offset")):
        return None
    source = SemanticSource(
        id=result["view_id"],
        view_id=result["view_id"],
        version=result["version"],
        fingerprint=result["model_fingerprint"],
    )
    try:
        query = QuerySpec.model_validate(
            {
                "source": source.id,
                **{
                    k: plan[k]
                    for k in (
                        "calculations",
                        "metrics",
                        "dimensions",
                        "filters",
                        "time",
                        "order_by",
                        "transforms",
                        "limit",
                        "named_filters",
                    )
                    if k in plan
                },
            }
        )
    except ValidationError:
        return None
    identity = str(uuid4())
    payload = {
        "source": source.model_dump(),
        "query": query.model_dump(),
        "sql_hash": digest(validate_artifact_sql(result["sql"])),
        "sql": validate_artifact_sql(result["sql"]),
    }
    await redis_client().set(
        f"nova:reports:artifact-proof:{scope(user)}:{identity}", json.dumps(payload), ex=86400
    )
    return identity


async def read_provenance(identity: str, sql: str, user: dict) -> dict:
    raw = await redis_client().get(f"nova:reports:artifact-proof:{scope(user)}:{identity}")
    if not raw:
        raise HTTPException(410, "Artifact provenance expired; run the query again")
    proof = json.loads(raw)
    if sql and proof["sql_hash"] != digest(validate_artifact_sql(sql)):
        raise HTTPException(422, "Artifact SQL does not match its producing semantic query")
    source = SemanticSource.model_validate(proof["source"])
    widget = Widget(id="source", title="Source", query=QuerySpec.model_validate(proof["query"]))
    await report_runtime.validate(
        ReportSpecV1(
            title="Source",
            sources=[source],
            pages=[
                Page(id="page", title="Source", sections=[Section(id="section", widgets=[widget])])
            ],
        ),
        user,
    )
    return proof


async def persist_provenance(artifact: dict, proof: dict, user: dict) -> None:
    identity = stable_id("artifact-binding", artifact["artifact_id"])
    async with metadata_lock(f"artifact-provenance:{identity}") as lease:
        await report_repository.append(
            record_id=identity,
            report_id=artifact["artifact_id"],
            kind="artifact_binding",
            owner=user["username"],
            operation_id=identity,
            expected_revision=0,
            request={"proof": proof},
            lease=lease,
            payload={"artifact_fingerprint": artifact_fingerprint(artifact), **proof},
        )


async def add_to_report(
    report_id: str,
    draft_id: str,
    artifact_id: str,
    section_id: str,
    operation_id: str,
    revision: int,
    user: dict,
) -> dict:
    artifact = await artifact_repository.get(artifact_id, owner_name=user["username"])
    if artifact is None:
        raise HTTPException(404, "Artifact not found")
    spec, _ = await report_service.spec(report_id, user, draft_id=draft_id, revision=revision)
    section = next((s for p in spec.pages for s in p.sections if s.id == section_id), None)
    if section is None:
        raise HTTPException(422, "Report section not found")
    binding = await report_repository.read(stable_id("artifact-binding", artifact_id))
    proof = binding["payload"] if binding else None
    mapped = proof is not None and proof["artifact_fingerprint"] == artifact_fingerprint(artifact)
    query = QuerySpec.model_validate(proof["query"]) if mapped and proof else None
    visual = artifact_visual(artifact, query) if query else None
    mapped = mapped and visual is not None
    placement = Placement(
        col=0,
        row=max((w.placement.row + w.placement.height for w in section.widgets), default=0),
        width=12,
        height=2,
    )
    widget = Widget(
        id=stable_id(report_id, operation_id, artifact_id),
        title=artifact["title"],
        **(visual or {"kind": "table"}),
        mapping_required=not mapped,
        legacy_artifact_id=artifact_id,
        placement=placement,
        query=query if mapped else None,
    )
    operations = []
    if mapped and proof:
        source = SemanticSource.model_validate(proof["source"])
        existing = next((s for s in spec.sources if s.id == source.id), None)
        if existing and existing != source:
            raise HTTPException(
                409, "Artifact uses a different semantic revision; map it explicitly"
            )
        if not existing:
            operations.append(
                ReportPatch(
                    op="SET_SOURCES",
                    value={
                        "sources": [s.model_dump() for s in spec.sources] + [source.model_dump()]
                    },
                )
            )
    operations.append(ReportPatch(op="ADD_WIDGET", parent=section_id, value=widget.model_dump()))
    result = await report_service.patch(
        report_id,
        draft_id,
        PatchRequest(operation_id=operation_id, expected_revision=revision, operations=operations),
        user,
    )
    return {"revision": result["revision"], "widget_id": widget.id, "requires_mapping": not mapped}
