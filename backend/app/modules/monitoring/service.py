"""Monitoring service — business logic for monitoring dashboards.

Thin wrapper that delegates to the repository layer.
Add data transformation or cross-cutting concerns here as the module evolves.
"""

from app.modules.monitoring.repository import monitoring_repo


class MonitoringService:
    """Orchestrate monitoring queries with optional business logic."""

    # ── Query History ────────────────────────────────────────────────

    async def get_query_history(self, **kwargs) -> dict:
        return await monitoring_repo.get_query_history(**kwargs)

    async def get_query_history_stats(self, **kwargs) -> dict:
        return await monitoring_repo.get_query_history_stats(**kwargs)

    # ── Audit Trail ──────────────────────────────────────────────────

    async def get_audit_trail(self, **kwargs) -> dict:
        return await monitoring_repo.get_audit_trail(**kwargs)

    # ── Active Queries ───────────────────────────────────────────────

    async def get_active_queries(self) -> list[dict]:
        return await monitoring_repo.get_active_queries()

    async def kill_query(self, connection_id: int) -> bool:
        return await monitoring_repo.kill_query(connection_id)

    # ── Tasks ────────────────────────────────────────────────────────

    async def get_task_runs(self, **kwargs) -> dict:
        return await monitoring_repo.get_task_runs(**kwargs)

    async def get_tasks(self) -> list[dict]:
        return await monitoring_repo.get_tasks()

    # ── Query Cost ───────────────────────────────────────────────────

    async def get_query_cost_history(self, **kwargs) -> dict:
        return await monitoring_repo.get_query_cost_history(**kwargs)

    async def get_cost_aggregation(self, **kwargs) -> list[dict]:
        return await monitoring_repo.get_cost_aggregation(**kwargs)

    async def get_fe_metrics_summary(self) -> dict:
        return await monitoring_repo.get_fe_metrics_summary()

    # ── Data Loads ───────────────────────────────────────────────────

    async def get_data_loads(self, **kwargs) -> dict:
        return await monitoring_repo.get_data_loads(**kwargs)

    async def get_load_stats(self) -> dict:
        return await monitoring_repo.get_load_stats()


monitoring_service = MonitoringService()
