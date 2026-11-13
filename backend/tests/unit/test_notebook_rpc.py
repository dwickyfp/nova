"""Kernel requests (con.write / con.merge_into / con.put) carried out by the worker."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.modules.notebooks.runtime import rpc
from app.modules.notebooks.runtime.provider import SandboxHandle

RID = "a" * 32
HANDLE = SandboxHandle(sandbox_id="sb", context_id="ctx")


def test_markers_are_taken_out_of_stderr_even_when_cut():
    reader = rpc.MarkerReader()
    text, ids = reader.feed(f"warn\n\x1enova-rpc {RID}\x1e\nmore")
    assert text == "warn\nmore" and ids == [RID]
    first, none = reader.feed(f"x\x1enova-rpc {RID[:10]}")
    assert first == "x" and none == []
    rest, found = reader.feed(f"{RID[10:]}\x1e\n")
    assert rest == "" and found == [RID]
    plain, nothing = reader.feed("an ordinary warning\n")
    assert plain == "an ordinary warning\n" and nothing == []


class Files:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = dict(files)
        self.written: dict[str, bytes] = {}

    async def read_file(self, handle, path):
        return self.files[path]

    async def write_file(self, handle, path, content):
        self.written[path] = content


def _parquet(table: pa.Table) -> bytes:
    sink = io.BytesIO()
    pq.write_table(table, sink)
    return sink.getvalue()


def _sandbox(request: dict, data: bytes, ext: str = "parquet") -> Files:
    request = {**request, "data": f"{RID}.{ext}"}
    return Files({f"{rpc.RPC_DIR}/{RID}.json": json.dumps(request).encode(),
                  f"{rpc.RPC_DIR}/{RID}.{ext}": data})


def _result(files: Files) -> dict:
    return json.loads(files.written[f"{rpc.RPC_DIR}/{RID}.result.json"])


@pytest.fixture
def caller(monkeypatch):
    async def resolve(session_id):
        return SimpleNamespace(username="alice", active_role="analyst",
                               security_context_version=3, encrypted_password="enc",
                               session_id=session_id)

    monkeypatch.setattr(rpc.data_tokens, "resolve_session", resolve)


@pytest.fixture
def statements(monkeypatch):
    ran: list[dict] = []

    class Service:
        async def execute_statements(self, **kwargs):
            ran.append(kwargs)
            return [SimpleNamespace(error=None)]

    import app.modules.query.service as query_module

    monkeypatch.setattr(query_module, "query_service", Service())
    return ran


async def test_a_nova_append_runs_as_the_user_with_escaped_values(caller, statements):
    files = _sandbox(
        {"op": "write", "mode": "append",
         "target": {"catalog": "default_catalog", "schema": "sales", "table": "t"}},
        _parquet(pa.table({"id": [1, 2], "note": ["it's", "a\\'); DROP TABLE x; --"]})),
    )
    await rpc.handle(files, HANDLE, "s1", RID)
    assert _result(files) == {"ok": True, "inserted": 2, "rows": 2}
    call = statements[0]
    assert call["username"] == "alice" and call["role"] == "analyst"
    assert call["security_context_version"] == 3 and call["source"] == "notebook"
    sql = call["sql"]
    assert sql.startswith("INSERT INTO `default_catalog`.`sales`.`t` (`id`, `note`) VALUES")
    assert "'it''s'" in sql and "\\\\" in sql  # quote doubled, backslash escaped


async def test_a_nova_merge_refuses_duplicate_keys_before_running(caller, statements):
    files = _sandbox(
        {"op": "merge", "on": ["id"],
         "target": {"catalog": "default_catalog", "schema": "s", "table": "t"}},
        _parquet(pa.table({"id": [1, 1], "v": ["a", "b"]})),
    )
    await rpc.handle(files, HANDLE, "s1", RID)
    assert _result(files)["ok"] is False and "same key" in _result(files)["error"]
    assert statements == []


async def test_a_nova_merge_uses_novas_merge(caller, statements):
    files = _sandbox(
        {"op": "merge", "on": ["ID"],
         "target": {"catalog": "default_catalog", "schema": "s", "table": "t"}},
        _parquet(pa.table({"id": [1, 2], "v": ["a", "b"]})),
    )
    await rpc.handle(files, HANDLE, "s1", RID)
    sql = statements[0]["sql"]
    assert sql.startswith("MERGE INTO `default_catalog`.`s`.`t` t USING (SELECT 1 AS `id`")
    assert "ON t.`id` = s.`id`" in sql and "WHEN MATCHED THEN UPDATE SET `v` = s.`v`" in sql


async def test_external_catalogs_go_to_write_back(caller, monkeypatch):
    calls = []

    async def write(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(inserted=1, updated=1, deleted=0)

    from app.modules.catalog import writeback

    monkeypatch.setattr(writeback, "write", write)
    files = _sandbox(
        {"op": "merge", "on": ["id"], "when_matched": "update",
         "target": {"catalog": "pg_shop", "schema": "public", "table": "orders"}},
        _parquet(pa.table({"id": [1, 2], "v": ["a", "b"]})),
    )
    await rpc.handle(files, HANDLE, "s1", RID)
    assert _result(files) == {"ok": True, "inserted": 1, "updated": 1, "deleted": 0, "rows": 2}
    assert calls[0]["target"] == "pg_shop.public.orders" and calls[0]["mode"] == "merge"
    assert calls[0]["username"] == "alice" and calls[0]["active_role"] == "analyst"


@pytest.fixture
def stages(monkeypatch, fake_metadata_db):
    uploads, checks, audits = [], [], []

    from app.modules.stages import access, service
    from tests.unit.stage_metadata_fakes import install_stages
    install_stages(monkeypatch, [
        {"id": "st-1", "name": "landing", "database_name": "raw", "schema_name": "files"},
        {"id": "st-2", "name": "dup", "database_name": "a", "schema_name": "x"},
        {"id": "st-3", "name": "dup", "database_name": "b", "schema_name": "x"},
    ])

    async def upload(stage_id, filename, content):
        uploads.append((stage_id["id"], filename, content))

    async def check(stage, *, action, username, active_role, **kwargs):
        checks.append((stage["id"], action, username, active_role))
        if active_role == "blocked":
            raise access.StageAccessDenied("Stage access denied")

    async def audit(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(service.stage_service, "upload_file", upload)
    monkeypatch.setattr("app.modules.stages.metadata_service.check_stage_access", check)
    monkeypatch.setattr("app.common.audit.write_audit_log", audit)
    return uploads, checks, audits


async def test_put_checks_stage_write_access_then_uploads(caller, stages):
    uploads, checks, audits = stages
    files = _sandbox({"op": "put", "target": "@landing/daily/2026-11-10.csv"}, b"a\n1\n", "csv")
    await rpc.handle(files, HANDLE, "s1", RID)
    assert _result(files) == {"ok": True, "path": "@landing/daily/2026-11-10.csv", "bytes": 4}
    assert checks == [("st-1", "write", "alice", "analyst")]
    assert uploads == [("st-1", "daily/2026-11-10.csv", b"a\n1\n")]
    assert audits[-1]["status"] == "SUCCESS"


@pytest.mark.parametrize(
    ("target", "message"),
    [("@dup/x.csv", "ambiguous"), ("@nowhere/x.csv", "not found"),
     ("@landing/../etc/x.csv", "not valid"), ("landing/x.csv", "@stage")],
)
async def test_put_refuses_unclear_or_unsafe_targets(caller, stages, target, message):
    uploads, _, _ = stages
    files = _sandbox({"op": "put", "target": target}, b"x", "csv")
    await rpc.handle(files, HANDLE, "s1", RID)
    assert message in _result(files)["error"] and uploads == []


async def test_a_role_without_stage_write_is_refused_and_audited(stages, monkeypatch):
    async def resolve(session_id):
        return SimpleNamespace(username="bob", active_role="blocked",
                               security_context_version=1, encrypted_password="e",
                               session_id=session_id)

    monkeypatch.setattr(rpc.data_tokens, "resolve_session", resolve)
    uploads, _, audits = stages
    files = _sandbox({"op": "put", "target": "@landing/x.csv"}, b"x", "csv")
    await rpc.handle(files, HANDLE, "s1", RID)
    assert _result(files) == {"ok": False, "error": "Stage access denied"}
    assert uploads == [] and audits[-1]["status"] == "DENIED"


async def test_an_ended_session_cannot_write(monkeypatch):
    async def resolve(session_id):
        raise rpc.data_tokens.BiCredentialError("gone")

    monkeypatch.setattr(rpc.data_tokens, "resolve_session", resolve)
    files = _sandbox({"op": "put", "target": "@landing/x.csv"}, b"x", "csv")
    await rpc.handle(files, HANDLE, "s1", RID)
    assert "Sign in" in _result(files)["error"]


async def test_oversized_data_is_refused(caller, monkeypatch):
    monkeypatch.setattr(rpc.settings, "NOTEBOOK_WRITE_MAX_BYTES", 2)
    files = _sandbox({"op": "put", "target": "@landing/x.csv"}, b"abc", "csv")
    await rpc.handle(files, HANDLE, "s1", RID)
    assert "at most" in _result(files)["error"]


async def test_an_unexpected_failure_reaches_the_kernel_as_a_safe_message(caller, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("internal detail with /secret/path")

    monkeypatch.setattr(rpc, "_put", boom)
    files = _sandbox({"op": "put", "target": "@landing/x.csv"}, b"x", "csv")
    await rpc.handle(files, HANDLE, "s1", RID)
    assert _result(files) == {"ok": False, "error": "Nova could not complete this request"}


async def test_the_worker_reads_large_displays_back_and_refuses_odd_paths(monkeypatch):
    from app.modules.notebooks.runtime.worker import NotebookRuntimeWorker

    good = "/tmp/nova/displays/" + "b" * 32 + ".json"
    files = Files({good: json.dumps({"text/html": "<div>chart</div>"}).encode()})
    worker = NotebookRuntimeWorker(provider=files)
    runtime = SimpleNamespace(id="rt", sandbox_id="sb", context_id="ctx")
    monkeypatch.setattr(rpc.settings, "NOTEBOOK_OUTPUT_MAX_BYTES", 10_000_000)
    out = await worker._read_display_files(runtime, [
        {"t": 1, "data": {"text/plain": "inline"}},
        {"t": 2, "file": good, "bytes": 30},
        {"t": 3, "file": "/etc/passwd", "bytes": 30},
        {"t": 4, "file": good, "bytes": 99_000_000},
    ])
    assert out[0]["data"] == {"text/plain": "inline"}
    assert out[1]["data"] == {"text/html": "<div>chart</div>"}
    assert len(out) == 3 and out[2]["data"] == {"text/plain": "Output too large to show"}
