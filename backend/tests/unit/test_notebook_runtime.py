"""Nova Notebook runtimes: lifecycle, queue, execution, cancel, security.

The sandbox is a fake provider that behaves like execd + nova_runtime: it
answers the trusted calls (bootstrap, begin, end, run_sql) with Nova payloads
and runs a tiny "user code" language (print, errors, sleeping). Redis is
fakeredis, so queues, leases, locks and streams are the real commands.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import fakeredis
import pytest

from app.core.config import settings
from app.core.redis import session_store
from app.core.security import encrypt_password
from app.modules.notebooks import packages as packages_module
from app.modules.notebooks.document import NotebookDocument, make_cell
from app.modules.notebooks.runtime import events, state
from app.modules.notebooks.runtime import service as service_module
from app.modules.notebooks.runtime import worker as worker_module
from app.modules.notebooks.runtime.provider import (
    KernelEvent,
    SandboxGone,
    SandboxHandle,
    SandboxMetrics,
    SandboxSpec,
)
from app.modules.notebooks.runtime.service import NotebookRuntimeError, NotebookRuntimeService
from app.modules.notebooks.runtime.worker import NotebookRuntimeWorker

PAYLOAD = "application/vnd.nova.payload+json"


class FakeProvider:
    def __init__(self) -> None:
        self.created: list[SandboxSpec] = []
        self.killed: list[str] = []
        self.ran: list[str] = []
        self.interrupted: list[str] = []
        self.files: dict[str, bytes] = {}
        self.allowed: list[list[str]] = []
        self.denied: list[list[str]] = []
        self.restarts = 0
        self.live: set[str] = set()
        self._interrupt = asyncio.Event()
        self.hang_forever = False

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        self.created.append(spec)
        sandbox_id = f"sb-{len(self.created)}"
        self.live.add(sandbox_id)
        return SandboxHandle(sandbox_id=sandbox_id, context_id="ctx-1")

    async def run(self, handle, code, sink) -> None:
        if handle.sandbox_id not in self.live:
            raise SandboxGone("gone")
        self.ran.append(code)
        await sink(KernelEvent(type="init", text=f"k-{len(self.ran)}"))
        if "__nova__." in code:
            call = re.search(r"__nova__\.(\w+)\((.*)\)$", code, re.S)
            name = call.group(1)
            arg = json.loads(eval(call.group(2))) if call.group(2) else {}  # noqa: S307
            payload = self._trusted(name, arg)
            await sink(KernelEvent(type="result", bundle={PAYLOAD: json.dumps(payload)}))
            return
        for line in code.splitlines():
            if line.startswith("print "):
                await sink(KernelEvent(type="stdout", text=line[6:] + "\n", timestamp=1.0))
            elif line == "fail":
                await sink(KernelEvent(type="error", error={
                    "name": "ZeroDivisionError", "value": "division by zero",
                    "traceback": ["\x1b[31mTraceback\x1b[0m", "1/0"]}))
            elif line == "sleep":
                self._interrupt.clear()
                await self._interrupt.wait()
                await sink(KernelEvent(type="error", error={
                    "name": "KeyboardInterrupt", "value": "", "traceback": []}))
            elif line == "oom":
                await sink(KernelEvent(type="error", error={
                    "name": "KernelDied", "traceback": [],
                    "value": "the kernel died while executing the code"}))
            elif line == "hang":
                await asyncio.Event().wait()
            elif line.startswith("show "):
                await sink(KernelEvent(type="result", bundle={"text/plain": line[5:]}))
        await sink(KernelEvent(type="complete"))

    def _trusted(self, name: str, arg: dict[str, Any]) -> dict[str, Any]:
        if name == "bootstrap":
            attached = bool(arg.get("credential_path"))
            return {"ready": True, "nova": {"attached": attached, "schemas": 3}}
        if name == "rotate":
            return {"rotated": True}
        if name == "begin":
            return {"started": True}
        if name == "end":
            return {"displays": [{"t": 2.0, "data": {"image/png": "iVBORw0KGgo="}}],
                    "variables": [{"name": "x", "type": "int"}]}
        if name == "run_sql":
            if "boom" in arg["source"]:
                return {"error": {"name": "CatalogException", "message": "Table boom not found"}}
            return {"kind": "table", "name": arg.get("name"), "table": {
                "schema": [{"name": "n", "type": "INTEGER"}], "rows": [[1]], "limit": 100,
                "total_rows": 1, "truncated": False, "origin": "local_duckdb"}}
        if name == "install":
            return {"ok": True, "versions": {arg["packages"][0]["name"]: "1.0"}}
        if name == "list_outputs":
            return {"files": []}
        return {}

    async def interrupt(self, handle, kernel_execution_id) -> None:
        self.interrupted.append(kernel_execution_id)
        self._interrupt.set()

    async def restart_context(self, handle):
        self.restarts += 1
        return SandboxHandle(handle.sandbox_id, f"ctx-{self.restarts + 1}")

    async def write_file(self, handle, path, content) -> None:
        self.files[path] = content

    async def read_file(self, handle, path) -> bytes:
        return self.files[path]

    async def metrics(self, handle):
        return SandboxMetrics(1, 5, 1024, 200)

    async def allow_hosts(self, handle, hosts) -> None:
        self.allowed.append(hosts)

    async def deny_hosts(self, handle, hosts) -> None:
        self.denied.append(hosts)

    async def kill(self, sandbox_id) -> None:
        self.killed.append(sandbox_id)
        self.live.discard(sandbox_id)

    async def list_runtime_sandboxes(self):
        return {sid: "unknown" for sid in self.live}

    async def close(self) -> None:
        return None


class Store:
    """In-memory notebook metadata and Workspace payloads."""

    def __init__(self) -> None:
        self.runtimes: list[dict] = []
        self.executions: list[dict] = []
        self.objects: dict[str, bytes] = {}
        self.packages: dict[str, list] = {}
        self.audits: list[dict] = []


#: alice's use of notebook nb-1: her runtime, runs and events.
CH = state.channel("nb-1", "alice")


@pytest.fixture
async def env(monkeypatch):
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(session_store, "_redis", redis)
    store = Store()
    repo = worker_module.notebook_repository

    async def record_runtime(actor, row):
        assert actor.principal == row["user_name"]
        store.runtimes.append(row)

    async def record_execution(actor, row):
        assert actor.principal == row["user_name"]
        store.executions.append(row)

    async def get_packages(user, notebook_id):
        return list(store.packages.get(notebook_id, []))

    async def modify_packages(actor, notebook_id, change):
        items = change(list(store.packages.get(notebook_id, [])))
        store.packages[notebook_id] = items
        return items

    async def latest_executions(user, notebook_id):
        return [dict(r, outputs=None) for r in store.executions]

    async def ensure_schema():
        return None

    for name, fn in {
        "record_runtime": record_runtime, "record_execution": record_execution,
        "get_packages": get_packages, "modify_packages": modify_packages,
        "latest_executions": latest_executions, "ensure_schema": ensure_schema,
    }.items():
        monkeypatch.setattr(repo, name, fn)

    ws = worker_module.workspace_service

    async def put_internal_object(key, body):
        store.objects[key] = body

    async def read_internal_object(key):
        return store.objects[key]

    async def list_entries(user):
        return [{"id": "nb-1", "path": "nb.ipynb", "parent_path": "", "entry_type": "file",
                 "name": "nb.ipynb", "size_bytes": 10}]

    monkeypatch.setattr(ws, "put_internal_object", put_internal_object)
    monkeypatch.setattr(ws, "read_internal_object", read_internal_object)
    monkeypatch.setattr(ws, "list_entries", list_entries)
    monkeypatch.setattr(ws, "internal_object_key", lambda user, *parts: "/".join((user, *parts)))

    async def audit(**kwargs):
        store.audits.append(kwargs)

    monkeypatch.setattr(worker_module, "write_audit_log", audit)
    monkeypatch.setattr(packages_module, "write_audit_log", audit)

    async def get_entry(user, entry_id):
        if user == "alice" and entry_id == "nb-1":
            return {"id": "nb-1", "name": "nb.ipynb", "entry_type": "file", "parent_path": ""}
        return None

    monkeypatch.setattr(service_module.workspace_service, "get_entry", get_entry)

    async def namespace_of(entry_id):
        return "alice" if entry_id == "nb-1" else None

    monkeypatch.setattr(service_module.workspace_service, "namespace_of", namespace_of)

    document = NotebookDocument(cells=[
        make_cell(kind="markdown", source="# Title", cell_id="md"),
        make_cell(kind="python", source="print hello", cell_id="c1"),
        make_cell(kind="sql", source="SELECT 1 AS n", name="one", cell_id="c2"),
        make_cell(kind="python", source="fail", cell_id="c3"),
        make_cell(kind="python", source="print after", cell_id="c4"),
    ], revision=5)

    async def get_document(user, notebook_id):
        if user != "alice" or notebook_id != "nb-1":
            raise NotebookRuntimeError("Notebook not found", status_code=404, code="NOT_FOUND")
        return {}, document

    monkeypatch.setattr(service_module.notebook_documents, "get", get_document)
    monkeypatch.setattr(settings, "OPENSANDBOX_API_KEY", "test-key")
    monkeypatch.setattr(settings, "NOTEBOOK_PROXY_ADDRESS", "0.250.250.254")
    monkeypatch.setattr(settings, "NOTEBOOK_INTERRUPT_GRACE_SECONDS", 1)

    session_id = await session_store.create(
        "alice", encrypt_password("pw"), ["analyst", "finance"],
        default_role="analyst", active_role="analyst",
    )
    user = {"username": "alice", "session_id": session_id, "active_role": "analyst",
            "assigned_roles": ["analyst", "finance"], "security_context_version": 1}
    provider = FakeProvider()
    worker = NotebookRuntimeWorker(provider, worker_id="w1")
    yield {"redis": redis, "store": store, "provider": provider, "worker": worker,
           "service": NotebookRuntimeService(), "user": user, "document": document}
    await redis.aclose()


async def drain(worker: NotebookRuntimeWorker) -> None:
    for _ in range(5):
        await worker._dispatch()
        tasks = list(worker._draining.values())
        if not tasks:
            return
        await asyncio.gather(*tasks)


async def events_of(redis, notebook_id="nb-1") -> list[tuple[str, dict]]:
    return [(f["event"], json.loads(f["data"]))
            for _id, f in await redis.xrange(state.events_key(state.channel(notebook_id, "alice")))]


async def started(env) -> state.RuntimeState:
    await env["service"].start(env["user"], "nb-1")
    await drain(env["worker"])
    runtime = await state.active_runtime(CH)
    assert runtime is not None and runtime.status == "READY", runtime
    return runtime


async def test_start_is_idempotent_and_reaches_ready_with_nova_attached(env):
    first = await env["service"].start(env["user"], "nb-1")
    second = await env["service"].start(env["user"], "nb-1")
    assert first["runtime"]["runtime_id"] == second["runtime"]["runtime_id"]
    await drain(env["worker"])
    runtime = await state.active_runtime(CH)
    assert runtime.status == "READY" and runtime.nova_attached
    assert len(env["provider"].created) == 1
    spec = env["provider"].created[0]
    assert spec.egress == [("0.250.250.254", settings.PROXY_PORT)]
    assert set(spec.env) == {"JUPYTER_TOKEN"}  # no data credential in the environment
    # The data token travelled as a one-time file, never in code.
    credential = next(
        v for k, v in env["provider"].files.items() if k.startswith("/tmp/nova/.cred-")
    )
    token = json.loads(credential)["token"]
    assert not any(token in code for code in env["provider"].ran)
    public = runtime.public()
    assert "sandbox_id" not in public and "session_id" not in public


async def test_quotas_refuse_extra_runtimes(env, monkeypatch):
    monkeypatch.setattr(settings, "NOTEBOOK_MAX_RUNTIMES_PER_USER", 1)
    await env["service"].start(env["user"], "nb-1")
    monkeypatch.setattr(service_module, "_require_notebook", _any_notebook)
    with pytest.raises(NotebookRuntimeError) as raised:
        await env["service"].start(env["user"], "nb-2")
    assert raised.value.code == "QUOTA_EXCEEDED" and raised.value.status_code == 429


async def _any_notebook(user, notebook_id, *, edit=False):
    from app.modules.workspaces.spaces import space_service

    return {"id": notebook_id, "parent_path": ""}, space_service.personal(user)


async def test_a_session_without_an_active_role_cannot_start(env):
    user = dict(env["user"], active_role=None)
    with pytest.raises(NotebookRuntimeError) as raised:
        await env["service"].start(user, "nb-1")
    assert raised.value.code == "FORBIDDEN"


async def test_another_users_notebook_is_not_found(env):
    bob = dict(env["user"], username="bob")
    with pytest.raises(NotebookRuntimeError) as raised:
        await env["service"].start(bob, "nb-1")
    assert raised.value.status_code == 404


async def test_run_all_executes_code_cells_in_order_and_stops_on_error(env):
    await started(env)
    response = await env["service"].run(
        env["user"], "nb-1", mode="all", cell_id=None, base_revision=5, on_error="stop",
        idempotency_key="k1",
    )
    assert [e["cell_id"] for e in response["executions"]] == ["c1", "c2", "c3", "c4"]
    again = await env["service"].run(
        env["user"], "nb-1", mode="all", cell_id=None, base_revision=5, on_error="stop",
        idempotency_key="k1",
    )
    assert again == response  # an HTTP retry never runs the cells twice
    await drain(env["worker"])
    by_cell = {row["cell_id"]: row for row in env["store"].executions}
    assert by_cell["c1"]["status"] == "succeeded"
    assert by_cell["c2"]["status"] == "succeeded" and by_cell["c2"]["sql_engine"] == "duckdb"
    assert by_cell["c3"]["status"] == "failed"
    assert by_cell["c3"]["error_name"] == "ZeroDivisionError"
    assert by_cell["c4"]["status"] == "skipped"
    outputs = json.loads(env["store"].objects[by_cell["c1"]["output_key"]])
    assert outputs[0] == {"type": "stream", "name": "stdout", "text": "hello\n"}
    assert outputs[-1]["type"] == "image"  # captured display merged by time
    error = json.loads(env["store"].objects[by_cell["c3"]["output_key"]])[0]
    assert error["traceback"][0] == "Traceback"  # terminal colours stripped
    assert not await env["redis"].hgetall(state.inflight_key(CH))
    kinds = [name for name, _ in await events_of(env["redis"])]
    assert kinds.count("cell.queued") == 4 and kinds.count("cell.finished") == 4
    assert "cell.output" in kinds
    runtime = await state.active_runtime(CH)
    assert runtime.status == "READY" and runtime.execution_counter == 3


async def test_continue_on_error_runs_the_rest(env):
    await started(env)
    await env["service"].run(env["user"], "nb-1", mode="below", cell_id="c3",
                             base_revision=5, on_error="continue", idempotency_key=None)
    await drain(env["worker"])
    assert [r["status"] for r in env["store"].executions] == ["failed", "succeeded"]


async def test_sql_cell_errors_are_reported_as_failures(env):
    await started(env)
    env["document"].cells[2].source = "SELECT * FROM boom"
    await env["service"].run(env["user"], "nb-1", mode="cell", cell_id="c2",
                             base_revision=5, on_error="stop", idempotency_key=None)
    await drain(env["worker"])
    row = env["store"].executions[0]
    assert row["status"] == "failed" and "boom" in row["error_message"]


async def test_running_needs_the_saved_revision_and_a_live_runtime(env):
    with pytest.raises(NotebookRuntimeError) as raised:
        await env["service"].run(env["user"], "nb-1", mode="all", cell_id=None,
                                 base_revision=5, on_error="stop", idempotency_key=None)
    assert raised.value.code == "RUNTIME_NOT_READY"
    await started(env)
    with pytest.raises(NotebookRuntimeError) as raised:
        await env["service"].run(env["user"], "nb-1", mode="all", cell_id=None,
                                 base_revision=4, on_error="stop", idempotency_key=None)
    assert raised.value.code == "NOTEBOOK_CONFLICT"


async def test_cancelling_a_queued_cell_never_runs_it(env):
    await started(env)
    response = await env["service"].run(env["user"], "nb-1", mode="cell", cell_id="c1",
                                        base_revision=5, on_error="stop", idempotency_key=None)
    ran_before = len(env["provider"].ran)
    cancelled = await env["service"].cancel(env["user"], "nb-1",
                                            response["executions"][0]["execution_id"])
    assert cancelled["cancelled"]
    await drain(env["worker"])
    assert env["store"].executions[0]["status"] == "cancelled"
    assert len(env["provider"].ran) == ran_before


async def test_cancelling_a_running_cell_interrupts_the_kernel(env):
    await started(env)
    env["document"].cells[1].source = "sleep"
    await env["service"].run(env["user"], "nb-1", mode="cell", cell_id="c1",
                             base_revision=5, on_error="stop", idempotency_key=None)
    task = asyncio.create_task(drain(env["worker"]))
    for _ in range(50):
        await asyncio.sleep(0.05)
        if (await state.active_runtime(CH)).status == "BUSY":
            break
    await env["service"].cancel(env["user"], "nb-1", None)
    await asyncio.wait_for(task, timeout=5)
    assert env["provider"].interrupted
    assert env["store"].executions[0]["status"] == "cancelled"


async def test_a_cell_that_ignores_interrupts_restarts_the_kernel(env, monkeypatch):
    monkeypatch.setattr(settings, "NOTEBOOK_CELL_TIMEOUT_SECONDS", 0)
    await started(env)
    env["document"].cells[1].source = "hang"
    await env["service"].run(env["user"], "nb-1", mode="cell", cell_id="c1",
                             base_revision=5, on_error="stop", idempotency_key=None)
    await asyncio.wait_for(drain(env["worker"]), timeout=10)
    assert env["store"].executions[0]["status"] == "timeout"
    assert env["provider"].restarts == 1
    runtime = await state.active_runtime(CH)
    assert runtime.status == "READY" and runtime.generation == 2
    assert "runtime.restarted" in [name for name, _ in await events_of(env["redis"])]


async def test_a_kernel_that_died_is_restarted_with_nova_bootstrapped(env):
    await started(env)
    env["document"].cells[1].source = "oom"
    await env["service"].run(env["user"], "nb-1", mode="cell", cell_id="c1",
                             base_revision=5, on_error="stop", idempotency_key=None)
    await asyncio.wait_for(drain(env["worker"]), timeout=10)
    execution = env["store"].executions[0]
    assert execution["status"] == "failed"
    assert "more memory" in execution["error_message"]
    assert env["provider"].restarts == 1
    runtime = await state.active_runtime(CH)
    assert runtime.status == "READY" and runtime.generation == 2
    # The new kernel ran Nova's bootstrap, so novaduck and SQL cells work again.
    assert any("__nova__.bootstrap" in code for code in env["provider"].ran[-3:])
    assert "runtime.restarted" in [name for name, _ in await events_of(env["redis"])]


async def test_cells_queued_before_a_restart_do_not_run_after_it(env):
    runtime = await started(env)
    await env["service"].run(env["user"], "nb-1", mode="cell", cell_id="c1",
                             base_revision=5, on_error="stop", idempotency_key=None)
    await state.update(runtime.id, generation=2)
    await drain(env["worker"])
    assert env["store"].executions[0]["status"] == "cancelled"


async def test_stop_kills_the_sandbox_and_revokes_nova_access(env):
    runtime = await started(env)
    assert await env["redis"].keys("nova:nb:token:*")
    await env["service"].stop(env["user"], "nb-1")
    await drain(env["worker"])
    assert env["provider"].killed == ["sb-1"]
    assert await state.active_runtime(CH) is None
    assert not await env["redis"].exists(state.rt_key(runtime.id))
    assert not await env["redis"].keys("nova:nb:token:*")
    status = await env["service"].status(env["user"], "nb-1")
    assert status["runtime"]["status"] == "STOPPED"


async def test_a_role_switch_rotates_the_runtime_credential(env):
    await started(env)
    await session_store.set_active_role(env["user"]["session_id"], "finance")
    user = dict(env["user"], active_role="finance", security_context_version=2)
    await env["service"].run(user, "nb-1", mode="cell", cell_id="c1", base_revision=5,
                             on_error="stop", idempotency_key=None)
    await drain(env["worker"])
    assert any("__nova__.rotate" in code for code in env["provider"].ran)
    runtime = await state.active_runtime(CH)
    assert runtime.token_role == "finance" and runtime.active_role == "finance"


async def test_a_failed_start_cleans_up(env, monkeypatch):
    async def broken(spec):
        raise SandboxGone("nope")

    monkeypatch.setattr(env["provider"], "create", broken)
    await env["service"].start(env["user"], "nb-1")
    await drain(env["worker"])
    assert await state.active_runtime(CH) is None
    status = await env["service"].status(env["user"], "nb-1")
    assert status["runtime"]["status"] == "FAILED" and status["runtime"]["error"]


async def test_idle_runtimes_are_stopped(env, monkeypatch):
    runtime = await started(env)
    await state.update(runtime.id, last_activity=0.0)
    await env["worker"]._sweep()
    await drain(env["worker"])
    assert await state.active_runtime(CH) is None
    assert env["store"].runtimes[-1]["end_reason"] == "idle"


async def test_reconcile_removes_orphans_and_marks_lost_runtimes(env):
    runtime = await started(env)
    env["provider"].live.add("sb-orphan")
    env["provider"].live.discard("sb-1")
    await env["worker"]._reconcile(startup=False)
    assert "sb-orphan" in env["provider"].killed
    assert await state.active_runtime(CH) is None
    assert (await state.last_outcome(CH))["status"] == "LOST"
    assert runtime.id


async def test_install_opens_the_index_only_while_installing(env):
    await started(env)
    result = await env["service"].install(env["user"], "nb-1", name="scikit-learn",
                                          version=None)
    assert result["package"]["import_name"] == "sklearn"
    await drain(env["worker"])
    assert env["provider"].allowed and env["provider"].denied
    assert env["store"].packages["nb-1"][0]["name"] == "scikit-learn"
    finished = [d for n, d in await events_of(env["redis"]) if n == "package.finished"]
    assert finished[-1]["ok"] is True


async def test_unapproved_packages_are_refused_before_anything_runs(env):
    await started(env)
    with pytest.raises(NotebookRuntimeError) as raised:
        await env["service"].install(env["user"], "nb-1", name="evil-package", version=None)
    assert raised.value.code == "PACKAGE_NOT_ALLOWED"
    with pytest.raises(NotebookRuntimeError):
        await env["service"].install(env["user"], "nb-1", name="scipy", version="1; rm -rf /")


async def test_event_stream_replays_after_the_last_seen_event(env, monkeypatch):
    async def shared():
        return env["redis"], False

    monkeypatch.setattr(events, "open_stream_client", shared)
    first = await events.publish("nb-1", "cell.queued", {"n": 1})
    await events.publish("nb-1", "cell.started", {"n": 2})

    async def never():
        return False

    frames = []
    stream = events.stream("nb-1", last_event_id=first, snapshot={"s": 1},
                           is_disconnected=never)
    async for frame in stream:
        frames.append(frame)
        if len(frames) == 2:
            break
    assert frames[0].startswith("event: snapshot")
    assert "cell.started" in frames[1] and "cell.queued" not in frames[1]


async def test_latest_merges_finished_and_in_flight_cells(env):
    await started(env)
    await env["service"].run(env["user"], "nb-1", mode="cell", cell_id="c1",
                             base_revision=5, on_error="stop", idempotency_key=None)
    latest = await env["service"].latest(env["user"], "nb-1")
    assert latest["inflight"][0]["cell_id"] == "c1" and latest["inflight"][0]["status"] == "queued"
    await drain(env["worker"])
    latest = await env["service"].latest(env["user"], "nb-1")
    assert not latest["inflight"] and latest["executions"][0]["status"] == "succeeded"
    assert latest["variables"] == [{"name": "x", "type": "int"}]



async def test_people_on_a_shared_notebook_each_get_their_own_runtime(env, monkeypatch):
    from app.modules.workspaces.spaces import Space

    shared = "~ws/" + "b" * 32
    permissions = {"alice": "edit", "bob": "view"}

    async def for_entry(user, entry_id, *, edit=False):
        return Space("b" * 32, "Team", "shared", shared, "carol", permissions[user["username"]])

    async def get_entry(namespace, entry_id):
        if namespace == shared and entry_id == "nb-1":
            return {"id": "nb-1", "name": "nb.ipynb", "entry_type": "file", "parent_path": ""}
        return None

    monkeypatch.setattr(service_module.space_service, "for_entry", for_entry)
    monkeypatch.setattr(service_module.workspace_service, "get_entry", get_entry)
    bob = {**env["user"], "username": "bob", "session_id": "bob-session"}

    mine = (await env["service"].start(env["user"], "nb-1"))["runtime"]["runtime_id"]
    theirs = (await env["service"].start(bob, "nb-1"))["runtime"]["runtime_id"]
    assert mine != theirs
    alice_runtime = await state.active_runtime(state.channel("nb-1", "alice"))
    bob_runtime = await state.active_runtime(state.channel("nb-1", "bob"))
    assert alice_runtime.user == "alice" and bob_runtime.user == "bob"
    assert alice_runtime.files_namespace == shared and alice_runtime.files_writable
    # A view-only member runs the notebook as themselves but writes nothing back.
    assert bob_runtime.files_namespace == shared and not bob_runtime.files_writable
    # Nothing is keyed by the notebook alone any more.
    assert await state.active_runtime("nb-1") is None
