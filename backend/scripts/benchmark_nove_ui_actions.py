"""Offline overhead benchmark for Nove's UI operation bridge."""

from __future__ import annotations

import asyncio
import json
from statistics import median, quantiles
from time import perf_counter
from types import SimpleNamespace

from app.core.deps import get_current_user
from app.main import app
from app.modules.assistant.tools import ToolInvocation, ui_actions
from app.modules.workspaces.service import workspace_service


def _stats(samples: list[float]) -> dict[str, float]:
    return {
        "median_ms": round(median(samples) * 1000, 3),
        "p95_ms": round(quantiles(samples, n=20)[18] * 1000, 3),
        "max_ms": round(max(samples) * 1000, 3),
    }


async def main() -> None:
    user = {
        "username": "benchmark",
        "session_id": "benchmark-session",
        "roles": ["analyst"],
        "assigned_roles": ["analyst"],
        "active_role": "analyst",
    }

    async def tree(_username: str) -> dict:
        return {"entries": []}

    async def audit(**_kwargs: object) -> str:
        return "benchmark-audit"

    previous_overrides = dict(app.dependency_overrides)
    previous_tree = workspace_service.get_tree
    previous_audit = ui_actions.write_audit_log
    app.dependency_overrides[get_current_user] = lambda: user
    workspace_service.get_tree = tree
    ui_actions.write_audit_log = audit
    try:
        start = perf_counter()
        operations = ui_actions._catalog()
        cold_catalog = perf_counter() - start

        search_call = ToolInvocation(
            "search", "find_ui_operation", {"query": "add data scope to role"}
        )
        read_call = ToolInvocation(
            "read", "call_ui_operation", {"operation": "GET /api/v1/workspaces/tree"}
        )
        context = SimpleNamespace(user=user, audit_session_id="benchmark-thread")
        samples: dict[str, list[float]] = {"catalog": [], "search": [], "preview": [], "api": []}
        for _ in range(200):
            start = perf_counter()
            ui_actions._catalog()
            samples["catalog"].append(perf_counter() - start)

            start = perf_counter()
            await ui_actions.find_ui_operation_tool.run(search_call, context)
            samples["search"].append(perf_counter() - start)

            start = perf_counter()
            ui_actions.call_ui_operation_tool.preview(read_call)
            samples["preview"].append(perf_counter() - start)

        for _ in range(50):
            start = perf_counter()
            outcome = await ui_actions.call_ui_operation_tool.run(read_call, context)
            samples["api"].append(perf_counter() - start)
            if not outcome.ok:
                raise RuntimeError(outcome.error)

        spec = app.openapi()
        total = sum(
            1
            for path, methods in spec["paths"].items()
            if path.startswith("/api/v1/")
            for method in methods
            if method in {"get", "post", "put", "patch", "delete"}
        )
        print(json.dumps({
            "api_operations": total,
            "catalog_operations": len(operations),
            "cold_catalog_ms": round(cold_catalog * 1000, 3),
            "samples": {name: _stats(values) for name, values in samples.items()},
        }, indent=2))
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        workspace_service.get_tree = previous_tree
        ui_actions.write_audit_log = previous_audit


if __name__ == "__main__":
    asyncio.run(main())
