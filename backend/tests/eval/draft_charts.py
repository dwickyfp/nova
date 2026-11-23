"""Fakes for the governed source behind Studio draft charts.

``data_to_chart`` inspects the result's statement as the caller before it
builds a native chart. These fakes stand in for that inspection so tests can
exercise chart building without an engine; the real inspection is covered by
the seeded governance tests.
"""

from __future__ import annotations

from typing import Any

from app.modules.viz import dataset_service, studio_publish
from app.modules.viz.contracts.authoring import SqlInspection
from app.modules.viz.contracts.datasets import DatasetField, SqlSource

SQL = "SELECT region, revenue FROM sales.orders"
#: A signed-in caller with one active role, as the Studio route passes it.
USER = {"username": "alice", "active_role": "ANALYST", "assigned_roles": ["ANALYST"],
        "session_id": "s1", "security_context_version": 1}


def _field(name: str, *, measure: bool, temporal: bool) -> DatasetField:
    if measure:
        logical, physical = "decimal", "DECIMAL(18,2)"
    elif temporal:
        logical, physical = "date", "DATE"
    else:
        logical, physical = "string", "VARCHAR"
    return DatasetField(field_id=name, name=name, source_column=name, logical_type=logical,
                        physical_type=physical,
                        semantic_role="measure" if measure else "dimension", nullable=True)


def install(monkeypatch: Any, columns: list[str], *, measures: set[str],
            temporal: frozenset[str] | set[str] = frozenset()) -> list[Any]:
    """Inspection of any statement returns ``columns``; returns the requests seen."""
    seen: list[Any] = []

    async def source_database(_sql: str, _user: dict[str, Any]) -> str:
        return "sales"

    async def inspect(_user: dict[str, Any], request: Any) -> SqlInspection:
        seen.append(request)
        source = SqlSource(
            sql=request.source.sql, ast_digest="b" * 64, catalog="default_catalog",
            database=request.source.database,
            dependencies=({"kind": "table", "catalog": "default_catalog", "database": "sales",
                           "object_name": "orders", "physical_id": "t1"},),
        )
        fields = tuple(_field(name, measure=name in measures, temporal=name in temporal)
                       for name in columns)
        return SqlInspection(dataset_id=request.dataset_id, source=source, fields=fields,
                             schema_fingerprint="c" * 64)

    monkeypatch.setattr(studio_publish, "source_database", source_database)
    monkeypatch.setattr(dataset_service.native_dataset_service, "inspect", inspect)
    return seen


def context(table: dict[str, Any], **extra: Any) -> Any:
    from types import SimpleNamespace

    values = {"last_result": table, "user": dict(USER),
              "thread_id": "thread-1", "steps": [], "evidence_tables": {}}
    values.update(extra)
    return SimpleNamespace(**values)
