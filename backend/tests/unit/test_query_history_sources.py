"""Worksheet history lists what was run from Workspace files.

A notebook runtime's DuckDB connection asks Nova for catalogs and column shapes
(SHOW CATALOGS, ... AS nova_prepare_describe LIMIT 0); those rows stay in the
audit log but are not the person's worksheet history.
"""

from __future__ import annotations

import pytest

from app.common.metadata_audit import audit_filter
from app.modules.query.service import QueryService


def test_files_only_selects_rows_with_a_workspace_file():
    where, params = audit_filter(event_type="query", user_name="ana", file_present=True).where()
    assert "file_id IS NOT NULL" in where and params == ["query", "ana"]
    where, _ = audit_filter(event_type="query", user_name="ana").where()
    assert "file_id IS NOT NULL" not in where


async def test_history_passes_files_only_to_the_audit_filter(monkeypatch):
    seen = {}

    class Reader:
        def __init__(self, connection):
            pass

        async def count(self, selection):
            seen["selection"] = selection
            return 0

        async def events(self, selection, columns, *, limit, offset):
            return []

    class Scope:
        connection = None

    class Transaction:
        async def __aenter__(self):
            return Scope()

        async def __aexit__(self, *exc):
            return False

    from app.modules.query import service as service_module

    monkeypatch.setattr(service_module, "AuditReader", Reader)
    monkeypatch.setattr(service_module, "scoped_transaction", lambda read_only=True: Transaction())
    result = await QueryService().get_history(username="ana", files_only=True)
    assert result == {"items": [], "total": 0}
    assert seen["selection"].file_present is True


@pytest.mark.parametrize("files_only", [False])
async def test_history_keeps_every_query_by_default(monkeypatch, files_only):
    from app.modules.query import service as service_module

    captured = {}
    original = service_module.audit_filter
    monkeypatch.setattr(
        service_module, "audit_filter", lambda **kw: captured.setdefault("f", original(**kw))
    )

    class Stop(Exception):
        pass

    def stop(read_only=True):
        raise Stop

    monkeypatch.setattr(service_module, "scoped_transaction", stop)
    with pytest.raises(Stop):
        await QueryService().get_history(username="ana", files_only=files_only)
    assert captured["f"].file_present is False
