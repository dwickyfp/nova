"""Runner adapters (H2): what the harness reads beside the event stream.

* ``TurnOutcome`` and its claims arrive on ``run.finished.outcome`` once PR-0
  ships; :mod:`fold` reads them. Until then the derived outcome is used, and
  claims may come from the thread's ``quality_observation`` step (below).
* A chart bound to a governed dataset carries no rows on the wire. After the
  turn the runner previews that dataset **as the user under test** and stores
  the rows in the record (``datasets``), so the grader sees what the chart drew.
* A deep-research run does not stream; its report and findings come from the
  deep-research state and become signals here.

Live calls use the runner's signed-in :class:`StudioClient` (the user under
test and its active role); nothing here signs in as an administrator.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tests.benchmark.studio_capability.v2.outcome import Claim

if TYPE_CHECKING:
    from tests.benchmark.studio_capability.client import StudioClient
    from tests.benchmark.studio_capability.v2.fold import Evidence, Signals

PREVIEW_ROWS = 200
TRACE_KINDS = frozenset({"quality_observation", "answer_verification"})


# ── offline: read what was attached ───────────────────────────────────

def dataset_evidence(record: dict[str, Any], bound: list[str]) -> list[Evidence]:
    from tests.benchmark.studio_capability.v2.fold import Evidence, num

    out: list[Evidence] = []
    datasets = _v if isinstance(_v := record.get("datasets"), dict) else {}
    for dataset_id in dict.fromkeys(bound):
        preview = datasets.get(dataset_id)
        if not isinstance(preview, dict) or preview.get("error"):
            continue
        names = [str(f.get("name")) if isinstance(f, dict) else str(f)
                 for f in preview.get("fields") or []]
        for row in preview.get("rows") or []:
            for index, cell in enumerate(row if isinstance(row, list) else []):
                value = num(cell)
                if value is not None:
                    out.append(Evidence(value, "dataset_preview", "dataset", dataset_id,
                                        names[index] if index < len(names) else None))
    return out


def trace_facts(record: dict[str, Any]) -> dict[str, Any]:
    """Facts of Nova's own checks (quality_observation, answer_verification)."""
    steps = _v if isinstance(_v := record.get("trace"), list) else []
    out: dict[str, Any] = {}
    for step in steps:
        if not isinstance(step, dict):
            continue
        if step.get("kind") == "quality_observation" and isinstance(step.get("facts"), dict):
            facts = step["facts"]
            out["facts"] = facts
            numeric = facts.get("numeric_consistency")
            claims = numeric.get("claims") if isinstance(numeric, dict) else None
            if isinstance(claims, list):
                out["claims"] = [Claim.model_validate(item) for item in claims
                                 if isinstance(item, dict)]
        elif step.get("kind") == "answer_verification":
            out["answer_verification"] = {k: step.get(k) for k in (
                "status", "evidence_columns", "unsupported", "language")}
    return out


def fold_deep_research(record: dict[str, Any], sig: Signals) -> Signals:
    """Signals of a deep-research unit: the report is prose, findings are tables."""
    from tests.benchmark.studio_capability.v2 import ui_tree
    from tests.benchmark.studio_capability.v2.fold import Evidence, num

    state = record.get("deep_research") or {}
    view = record.get("view") or {}
    report = str(view.get("answer_text") or state.get("report") or "")
    sig.answer_chars = len(report.strip())
    sig.deep_research = {"status": state.get("status"),
                         "plan": len(state.get("plan") or []),
                         "investigations": len(state.get("progress") or []),
                         "failed": sum(1 for p in state.get("progress") or []
                                       if isinstance(p, dict) and p.get("status") != "done")}
    status = state.get("status")
    sig.finish_reason = "stop" if status == "done" and report else (
        "error" if status in {None, "failed", "error"} else str(status))
    if record.get("transport_error"):
        sig.run_errors.append({"code": "transport"})
    for finding in state.get("findings") or []:
        table = finding.get("table") if isinstance(finding, dict) else None
        if not isinstance(table, dict):
            continue
        columns = [str(c) for c in table.get("columns") or []]
        rows = [r for r in table.get("rows") or [] if isinstance(r, list)]
        call = finding.get("tool_call_id")
        sig.tables.append({"columns": columns, "rows": rows, "tool": "semantic_query",
                           "call": call})
        for row in rows:
            for index, cell in enumerate(row):
                value = num(cell)
                if value is not None:
                    sig.evidence.append(Evidence(value, "table", "semantic_query", call,
                                                 columns[index] if index < len(columns)
                                                 else None))
        sig.tools.append({"name": "semantic_query", "call": call, "status": "done",
                          "reason": None, "classification": None, "exec": "success",
                          "reasons": []})
    sig.usage = dict(state.get("usage") or {})
    sig.elements = ui_tree.kinds([], tables=len(sig.tables), text=bool(sig.answer_chars))
    return sig


# ── live: attach after a turn (no model calls) ────────────────────────

def bound_datasets(events: list[dict[str, Any]]) -> list[tuple[str, int]]:
    """(dataset_id, revision) of every DatavizChart component part in a journal."""
    out: dict[str, int] = {}
    for entry in events:
        data = _v if isinstance(_v := entry.get("data"), dict) else {}
        block = _v if isinstance(_v := data.get("block"), dict) else {}
        if entry.get("event") != "part.start" or block.get("component") != "DatavizChart":
            continue
        chart = (block.get("props") or {}).get("chart") or {}
        if isinstance(chart, dict) and chart.get("dataset_id"):
            out[str(chart["dataset_id"])] = int(chart.get("dataset_revision") or 1)
    return list(out.items())


async def attach(client: StudioClient, record: dict[str, Any], agent_id: str) -> None:
    """Dataset previews (as the user) and the thread's trace steps, into the record."""
    datasets: dict[str, Any] = {}
    for dataset_id, revision in bound_datasets(record.get("events") or []):
        try:
            preview = await client.request(
                "POST", f"viz/datasets/{dataset_id}/preview",
                {"revision": revision, "draft": True, "limit": PREVIEW_ROWS})
            datasets[dataset_id] = {"fields": [{"name": f.get("name")}
                                               for f in preview.get("fields") or []],
                                    "rows": preview.get("rows") or []}
        except Exception as exc:  # noqa: BLE001 - recorded; the grader treats it as absent
            datasets[dataset_id] = {"error": type(exc).__name__}
    if datasets:
        record["datasets"] = datasets
    if record.get("thread_id") and record.get("run_id"):
        try:
            thread = await client.thread(agent_id, record["thread_id"])
        except Exception as exc:  # noqa: BLE001
            record["trace_error"] = type(exc).__name__
            return
        record["trace"] = _steps(thread, str(record.get("run_id")))


def _steps(thread: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
    messages = _v if isinstance(_v := thread.get("messages"), list) else []
    last: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        steps = _v if isinstance(_v := message.get("steps"), list) else []
        kept = [s for s in steps if isinstance(s, dict) and s.get("kind") in TRACE_KINDS]
        if message.get("run_id") == run_id:
            return kept
        last = kept
    return last  # messages without a run id: the latest answer is this turn's
