"""What the Dataviz assistant may know about the dashboard on screen.

Built for one caller from three verified sources (docs/arch-19-studio-dataviz.md,
D5 and D6), never from what the browser claims:

* Superset's REST API in the caller's own BI session: the dashboard, its charts
  and datasets, owners and revision. A dashboard the caller cannot open is not
  described at all.
* Superset's filter state API for the ``native_filters_key`` in the framed URL:
  the only way a filter is reported as ``verified``. Without a key the state is
  ``unavailable``; a key that no longer resolves is ``stale``.
* The Supernova Business Ontology: a chart's dataset is matched to its Semantic
  View (``CONFIG_SEMANTIC_BI_DATASETS``), each saved metric it uses to the
  approved concept bound to that view metric, and a chart Nova published keeps
  the ``business_context`` of the query that produced it. Concepts the caller's
  role cannot see are left out, and seeing a concept never grants data access.
  Titles are also resolved lexically; those are marked as name matches.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

from app.core.database import db

from .client import BiError, SupersetClient

logger = logging.getLogger(__name__)

FilterStatus = Literal["verified", "partial", "unavailable", "stale"]
_KEY = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")
MAX_CHARTS = 60


def _json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "") if value else default
    except (TypeError, ValueError):
        return default


def _metric_names(form: dict[str, Any]) -> list[str]:
    names: list[str] = []
    raw = form.get("metrics") or ([form["metric"]] if form.get("metric") else [])
    for metric in raw:
        if isinstance(metric, str):
            names.append(metric)
        elif isinstance(metric, dict):
            label = metric.get("label") or (metric.get("column") or {}).get("column_name")
            if label:
                names.append(str(label))
    return names


def _dimensions(form: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("x_axis", "groupby", "columns", "all_columns", "series"):
        value = form.get(key)
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, str) and item:
                out.append(item)
            elif isinstance(item, dict) and item.get("label"):
                out.append(str(item["label"]))
    return list(dict.fromkeys(out))


def _filters(metadata: dict[str, Any], state: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Native filters with the values Superset stored for this view."""
    out = []
    for item in metadata.get("native_filter_configuration") or []:
        if not isinstance(item, dict) or item.get("type") == "DIVIDER":
            continue
        targets = item.get("targets") or [{}]
        column = ((targets[0] or {}).get("column") or {}).get("name")
        default = ((item.get("defaultDataMask") or {}).get("filterState") or {}).get("value")
        entry: dict[str, Any] = {
            "id": item.get("id"),
            "name": item.get("name"),
            "column": column,
            "kind": item.get("filterType"),
            "scope": "whole dashboard"
            if not ((item.get("scope") or {}).get("excluded"))
            and (item.get("scope") or {}).get("rootPath") == ["ROOT_ID"]
            else "some charts",
            "default": default,
        }
        if state is not None:
            current = (state.get(str(item.get("id"))) or {}).get("filterState") or {}
            entry["value"] = current.get("value")
        out.append(entry)
    return out


async def _filter_state(
    bi: SupersetClient, dashboard_id: int, key: str | None
) -> tuple[FilterStatus, dict | None]:
    if not key:
        return "unavailable", None
    if not _KEY.match(key):
        return "stale", None
    try:
        body = await bi.request("GET", f"/api/v1/dashboard/{dashboard_id}/filter_state/{key}")
    except BiError as exc:
        if exc.status in (404, 422, 400):
            return "stale", None
        raise
    value = _json(body.get("value"), None)
    if not isinstance(value, dict):
        return "stale", None
    # Superset stores either the data mask itself or {"dataMask": ..., "activeTabs": ...}.
    mask = value.get("dataMask") if isinstance(value.get("dataMask"), dict) else value
    return "verified", mask


async def _semantic_views(dataset_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not dataset_ids:
        return {}
    marks = ", ".join(["%s"] * len(dataset_ids))
    try:
        result = await db.execute_system(
            "SELECT m.bi_dataset_id, m.view_id, m.version, m.status, v.name "
            "FROM NOVA_SYSTEM.CONFIG_SEMANTIC_BI_DATASETS m "
            "LEFT JOIN NOVA_SYSTEM.CONFIG_SEMANTIC_VIEWS v ON v.id = m.view_id "
            f"WHERE m.bi_dataset_id IN ({marks})",
            dataset_ids,
        )
    except Exception as exc:  # noqa: BLE001 - no sync table yet means no synced views
        logger.info("Semantic dataset map unavailable: %s", type(exc).__name__)
        return {}
    return {
        int(row[0]): {
            "view_id": row[1],
            "version": row[2],
            "status": row[3],
            "name": row[4] if len(row) > 4 else None,
        }
        for row in result.get("rows") or []
        if row[0] is not None
    }


async def _published_contexts(chart_ids: list[int]) -> dict[int, dict[str, Any]]:
    """The ontology context Nova recorded when it published each chart."""
    if not chart_ids:
        return {}
    marks = ", ".join(["%s"] * len(chart_ids))
    try:
        result = await db.execute_system(
            "SELECT chart_id, business_context FROM NOVA_SYSTEM.CONFIG_DATAVIZ_PUBLICATIONS "
            f"WHERE status = 'SUCCEEDED' AND chart_id IN ({marks}) ORDER BY updated_at",
            chart_ids,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("Publication journal unavailable: %s", type(exc).__name__)
        return {}
    out = {}
    for chart_id, business in result.get("rows") or []:
        parsed = _json(business, None)
        if isinstance(parsed, dict):
            out[int(chart_id)] = parsed
    return out


class _Ontology:
    """Ontology lookups for one caller, from the current release only."""

    def __init__(self, user: dict[str, Any]) -> None:
        self.user = user
        self.snapshot: Any = None
        self.available = True

    async def load(self) -> None:
        from app.core.metadata_scope import acting_as, actor_for
        from app.modules.ontology.service import ontology_service

        try:
            async with acting_as(actor_for(self.user)):
                self.snapshot = await ontology_service.snapshot("current")
        except Exception as exc:  # noqa: BLE001 - the context still describes the BI side
            logger.info("Ontology unavailable for Dataviz context: %s", type(exc).__name__)
            self.available = False

    def _visible(self) -> dict[str, Any]:
        from app.modules.ontology import access
        from app.modules.ontology.service import ontology_service

        if self.snapshot is None or self.snapshot.release is None:
            return {}
        # This context feeds the Dataviz assistant's prompt, so a definition
        # whose classification keeps it from models is left out here too.
        domains = self.snapshot.domains
        return {
            concept_id: concept
            for concept_id, concept in ontology_service.visible_concepts(
                self.snapshot, self.user
            ).items()
            if access.model_may_see(
                concept.classification, domains.get(concept.domain_id), domains, self.user
            )
        }

    def _view(self, concept: Any) -> dict[str, Any]:
        from app.modules.ontology.service import ontology_service

        view = ontology_service.concept_view(concept, self.snapshot.domains)
        return {
            key: view.get(key) for key in ("id", "qualified_name", "label", "definition", "kind")
        }

    def bound(self, view_id: str, metric: str) -> list[dict[str, Any]]:
        visible = self._visible()
        out = []
        for item in getattr(self.snapshot, "representations", None) or []:
            if (
                item.semantic.view_id == view_id
                and item.metric == metric
                and item.status == "VALIDATED"
                and item.concept_id in visible
            ):
                out.append(
                    {
                        **self._view(visible[item.concept_id]),
                        "link": "binding",
                        "metric": metric,
                        "view_id": view_id,
                    }
                )
        return out

    def recorded(self, business: dict[str, Any]) -> list[dict[str, Any]]:
        visible = self._visible()
        return [
            {
                **self._view(visible[item["id"]]),
                "link": "published_with",
                "metric": item.get("metric"),
                "release_id": business.get("ontology_release_id"),
            }
            for item in business.get("concepts") or []
            if isinstance(item, dict) and item.get("id") in visible
        ]

    async def named(self, text: str) -> list[dict[str, Any]]:
        from app.modules.ontology.resolver import ConceptResolver

        if self.snapshot is None or self.snapshot.release is None or not text.strip():
            return []
        try:
            resolution = await ConceptResolver().resolve(text, self.user, snapshot=self.snapshot)
        except Exception as exc:  # noqa: BLE001
            logger.info("Concept resolution failed: %s", type(exc).__name__)
            return []
        return [
            {key: item.get(key) for key in ("id", "qualified_name", "label", "definition")}
            | {"link": "name_match", "terms": resolution.terms.get(str(item.get("id")), [])}
            for item in resolution.concepts
        ]

    def release(self) -> dict[str, Any] | None:
        if self.snapshot is None or self.snapshot.release is None:
            return None
        return {
            "release_id": self.snapshot.release.id,
            "manifest_hash": self.snapshot.release.manifest_hash,
        }


async def dashboard_context(
    user: dict[str, Any],
    dashboard_id: int,
    *,
    native_filters_key: str | None = None,
    edit_state: Literal["clean", "dirty", "unknown"] = "unknown",
) -> dict[str, Any]:
    """The verified context of one dashboard for this caller."""
    from .access import superset_role_for
    from .publication import can_edit, me, read_dashboard, revision

    superset_role = await superset_role_for(str(user.get("active_role") or ""))
    async with SupersetClient.for_user(user, author=False) as bi:
        try:
            dashboard = await read_dashboard(bi, int(dashboard_id))
        except BiError as exc:
            if exc.status in (403, 404):
                return {
                    "dashboard_id": dashboard_id,
                    "available": False,
                    "reason": "This dashboard is not available to your active role.",
                }
            raise
        current = await me(bi)
        charts = (await bi.request("GET", f"/api/v1/dashboard/{dashboard_id}/charts")).get(
            "result"
        ) or []
        datasets = (await bi.request("GET", f"/api/v1/dashboard/{dashboard_id}/datasets")).get(
            "result"
        ) or []
        status, mask = await _filter_state(bi, int(dashboard_id), native_filters_key)

    metadata = _json(dashboard.get("json_metadata"), {})
    layout = _json(dashboard.get("position_json"), {})
    ordered = (
        [
            (node.get("meta") or {}).get("chartId")
            for node in layout.values()
            if isinstance(node, dict) and node.get("type") == "CHART"
        ]
        if isinstance(layout, dict)
        else []
    )
    order = {chart_id: index for index, chart_id in enumerate(ordered)}
    dataset_by_id = {int(d["id"]): d for d in datasets if d.get("id") is not None}
    semantic = await _semantic_views(list(dataset_by_id))
    published = await _published_contexts([int(c["id"]) for c in charts if c.get("id") is not None])
    ontology = _Ontology(user)
    await ontology.load()
    author = superset_role != "NovaViewer"

    chart_rows = []
    for chart in sorted(charts, key=lambda c: order.get(c.get("id"), 10_000))[:MAX_CHARTS]:
        form = chart.get("form_data") or {}
        dataset_ref = str(form.get("datasource") or "")
        dataset_id = (
            int(dataset_ref.split("__")[0]) if dataset_ref.split("__")[0].isdigit() else None
        )
        dataset = dataset_by_id.get(dataset_id or -1, {})
        view = semantic.get(dataset_id or -1)
        metrics = _metric_names(form)
        concepts: list[dict[str, Any]] = []
        if view:
            for metric in metrics:
                concepts.extend(ontology.bound(view["view_id"], metric))
        if int(chart["id"]) in published:
            concepts.extend(ontology.recorded(published[int(chart["id"])]))
        source: dict[str, Any] = {
            "dataset_id": dataset_id,
            "dataset": dataset.get("table_name") or dataset.get("datasource_name"),
            "kind": "semantic_view" if view else ("virtual" if dataset.get("sql") else "table"),
            "schema": dataset.get("schema"),
        }
        if view:
            source["semantic_view"] = view
        if author and dataset.get("sql"):
            source["sql"] = str(dataset["sql"])[:4000]
        chart_rows.append(
            {
                "chart_id": chart.get("id"),
                "title": chart.get("slice_name"),
                "viz_type": form.get("viz_type"),
                "metrics": metrics,
                "dimensions": _dimensions(form),
                "time_grain": form.get("time_grain_sqla"),
                "source": source,
                "concepts": concepts,
                "nova_published": int(chart["id"]) in published,
            }
        )
    named = await ontology.named(
        " ".join(
            [str(dashboard.get("dashboard_title") or "")]
            + [str(c["title"] or "") for c in chart_rows]
        )
    )
    filters = _filters(metadata, mask)
    if not filters:
        # A dashboard with no native filters has nothing to select: its
        # filter state is known (empty), whatever the URL carries.
        status = "verified"
    gaps = []
    if status != "verified":
        gaps.append(
            "The dashboard's current filter selection could not be read; analyses use no filters "
            "unless the user states them."
        )
    unlinked = [c["title"] for c in chart_rows if not c["concepts"]]
    if unlinked:
        gaps.append(
            "No ontology concept is bound to: " + ", ".join(str(t) for t in unlinked[:10]) + "."
        )
    if not ontology.available:
        gaps.append("The business ontology could not be read.")
    return {
        "available": True,
        "dashboard_id": dashboard.get("id"),
        "title": dashboard.get("dashboard_title"),
        "published": dashboard.get("published"),
        "owners": [
            f"{o.get('first_name', '')} {o.get('last_name', '')}".strip() or o.get("username")
            for o in dashboard.get("owners") or []
        ],
        "changed": dashboard.get("changed_on_delta_humanized"),
        "revision": revision(dashboard),
        "can_edit": can_edit(dashboard, current, superset_role),
        "role": user.get("active_role"),
        "edit_state": edit_state,
        "filter_state_status": status,
        "filters_configured": len(filters),
        "filters": filters,
        "charts": chart_rows,
        "datasets": [
            {
                "dataset_id": dataset_id,
                "name": dataset.get("table_name") or dataset.get("datasource_name"),
                "semantic_view": semantic.get(dataset_id),
                "metrics": [
                    str(m.get("metric_name"))
                    for m in dataset.get("metrics") or []
                    if m.get("metric_name")
                ][:40],
                "columns": [
                    str(c.get("column_name"))
                    for c in dataset.get("columns") or []
                    if c.get("column_name")
                ][:80],
                "time_columns": [
                    str(c.get("column_name"))
                    for c in dataset.get("columns") or []
                    if c.get("is_dttm")
                ],
            }
            for dataset_id, dataset in dataset_by_id.items()
        ],
        "ontology": {"release": ontology.release(), "named_in_titles": named},
        "gaps": gaps,
        "bi_view_path": f"/bi/superset/dashboard/{dashboard.get('id')}/",
    }


async def chart_source(user: dict[str, Any], chart_id: int) -> dict[str, Any]:
    """Where one chart's numbers come from, as far as the caller may see."""
    from .access import superset_role_for

    superset_role = await superset_role_for(str(user.get("active_role") or ""))
    async with SupersetClient.for_user(user, author=False) as bi:
        try:
            chart = (await bi.request("GET", f"/api/v1/chart/{int(chart_id)}")).get("result") or {}
        except BiError as exc:
            if exc.status in (403, 404):
                return {
                    "chart_id": chart_id,
                    "available": False,
                    "reason": "This chart is not available to your active role.",
                }
            raise
        form = _json(chart.get("params"), {})
        dataset_id = None
        ref = str(form.get("datasource") or "")
        if ref.split("__")[0].isdigit():
            dataset_id = int(ref.split("__")[0])
        dataset: dict[str, Any] = {}
        if dataset_id:
            try:
                dataset = (await bi.request("GET", f"/api/v1/dataset/{dataset_id}")).get(
                    "result"
                ) or {}
            except BiError:
                dataset = {}
    view = (await _semantic_views([dataset_id] if dataset_id else [])).get(dataset_id or -1)
    published = (await _published_contexts([int(chart_id)])).get(int(chart_id))
    ontology = _Ontology(user)
    await ontology.load()
    metrics = _metric_names(form)
    concepts = []
    if view:
        for metric in metrics:
            concepts.extend(ontology.bound(view["view_id"], metric))
    if published:
        concepts.extend(ontology.recorded(published))
    lineage: list[str] = [f"chart “{chart.get('slice_name')}”"]
    if dataset:
        lineage.append(f"dataset {dataset.get('table_name')}")
    if view:
        lineage.append(f"Semantic View {view['view_id']} (version {view.get('version')})")
    for concept in concepts:
        lineage.append(f"concept {concept['qualified_name']}")
    gaps = []
    if not dataset:
        gaps.append("The chart's dataset is not readable with your active role.")
    if not concepts:
        gaps.append("No approved ontology concept is linked to this chart's metrics.")
    return {
        "available": True,
        "chart_id": chart_id,
        "title": chart.get("slice_name"),
        "viz_type": chart.get("viz_type"),
        "metrics": metrics,
        "dimensions": _dimensions(form),
        "filters": form.get("adhoc_filters") or [],
        "dataset": {
            "id": dataset_id,
            "name": dataset.get("table_name"),
            "schema": dataset.get("schema"),
            "kind": "semantic_view" if view else ("virtual" if dataset.get("sql") else "table"),
            "sql": str(dataset.get("sql") or "")[:4000] if superset_role != "NovaViewer" else None,
            "columns": [c.get("column_name") for c in dataset.get("columns") or []][:80],
        },
        "semantic_view": view,
        "concepts": concepts,
        "description": chart.get("description"),
        "lineage": lineage,
        "gaps": gaps,
        "ontology_release": ontology.release(),
    }


def brief(context: dict[str, Any]) -> str:
    """The open dashboard for the assistant's prompt: ids, fields, filters, meaning.

    Built from :func:`dashboard_context`, so everything in it was verified as
    the caller. It names no SQL and no rows.
    """
    if not context.get("available"):
        return f"The open Dataviz dashboard is not available: {context.get('reason')}"
    lines = [
        f"Open Dataviz dashboard {context['dashboard_id']}: “{context.get('title')}”"
        f" ({'can edit' if context.get('can_edit') else 'view only'}, role {context.get('role')}).",
        "Charts: "
        + "; ".join(
            f"#{c['chart_id']} {c['title']} ({c['viz_type']}: {', '.join(c['metrics']) or '-'}"
            f" by {', '.join(c['dimensions']) or '-'}; dataset {c['source'].get('dataset_id')})"
            for c in context.get("charts", [])[:20]
        ),
    ]
    for dataset in context.get("datasets", []):
        view = dataset.get("semantic_view") or {}
        lines.append(
            f"Dataset {dataset['dataset_id']} {dataset.get('name')}"
            + (f" = Semantic View “{view.get('name')}” v{view.get('version')}" if view else "")
            + f". Saved metrics: {', '.join(dataset['metrics']) or 'none'}."
            + f" Columns: {', '.join(dataset['columns'])}."
            + (
                f" Time columns: {', '.join(dataset['time_columns'])}."
                if dataset["time_columns"]
                else ""
            )
        )
    status = context.get("filter_state_status")
    if not context.get("filters"):
        lines.append("Filters: the dashboard has no native filters.")
    elif status == "verified":
        chosen = [f"{f['name']} = {f.get('value')}" for f in context["filters"] if f.get("value")]
        lines.append(
            "Filters (verified): " + ("; ".join(chosen) if chosen else "none selected") + "."
        )
    else:
        lines.append(
            f"Filters: {', '.join(f['name'] for f in context['filters'])}; their current "
            f"selection is {status}, so say it is unknown."
        )
    concepts = {
        c.get("qualified_name"): c.get("definition")
        for chart in context.get("charts", [])
        for c in chart.get("concepts", [])
        if c.get("qualified_name")
    }
    concepts |= {
        c.get("qualified_name"): c.get("definition")
        for c in (context.get("ontology") or {}).get("named_in_titles", [])
        if c.get("qualified_name") and c.get("qualified_name") not in concepts
    }
    if concepts:
        lines.append(
            "Business concepts: "
            + "; ".join(f"{name}: {definition}" for name, definition in list(concepts.items())[:10])
        )
    return "\n".join(lines)
