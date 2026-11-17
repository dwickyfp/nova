"""Nova pipes: statement decoding, planning, batching, labels and the worker's run.

The run is exercised against an in-memory ledger that keeps the PostgreSQL
lifecycle rules (UNLOADED -> LOADING -> FINISHED | ERROR | UNLOADED) and a fake
engine that remembers committed labels, as StarRocks does.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.modules.pipes import runner as runner_module
from app.modules.pipes.definition import (
    PipeDefinitionError,
    PipeFile,
    PipeProperties,
    batch_label,
    batches,
    decode_create,
    glob_regex,
    list_prefix,
    parse_properties,
    parse_size,
    render_batch,
)
from app.modules.pipes.repository import LeaseLost
from app.modules.pipes.runner import (
    EngineFailure,
    LabelState,
    PipeRun,
    PipeRuntime,
    classify,
    safe_message,
)
from app.sql_frontend.analysis.semantics import semantics_registry
from app.sql_frontend.ast import statements as ast
from app.sql_frontend.ast.builder import ast_builders
from app.sql_frontend.context import PlanningContext
from app.sql_frontend.errors import CapabilityUnsupportedError
from app.sql_frontend.parser import parse_statement
from app.sql_frontend.planning.execution import NovaActionPlan
from app.sql_frontend.planning.planner import SQLPlanner

SECRET = "SECRETKEY-do-not-leak-0123456789"
CREATE = (
    'CREATE PIPE sales.orders_pipe PROPERTIES ("AUTO_INGEST" = "TRUE", "POLL_INTERVAL" = "10", '
    '"BATCH_SIZE" = "1MB", "BATCH_FILES" = "2") AS INSERT INTO orders (id, name) '
    'SELECT $1, $2 FROM FILES("path" = "@landing/orders/*.parquet", "format" = "parquet")'
)


def plan(sql: str, database: str | None = "sales"):
    statement = ast_builders.build(parse_statement(sql))
    return statement, asyncio.run(SQLPlanner().plan(statement, PlanningContext(database=database)))


# ── statements ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(("sql", "operation", "action"), [
    (CREATE, "create", None),
    ("CREATE OR REPLACE PIPE p AS INSERT INTO t SELECT * FROM @landing/x/*.csv", "create", None),
    ("CREATE PIPE IF NOT EXISTS p AS INSERT INTO t SELECT * FROM @landing/x/*.csv", "create",
     None),
    ("ALTER PIPE p SUSPEND", "alter", "suspend"),
    ("ALTER PIPE sales.p RESUME", "alter", "resume"),
    ("ALTER PIPE p RETRY ALL", "alter", "retry_all"),
    ("ALTER PIPE p RETRY FILE '@landing/x/a.csv'", "alter", "retry_file"),
    ('ALTER PIPE p SET ("POLL_INTERVAL" = "60", "AUTO_INGEST" = "FALSE")', "alter", "set"),
    ("DROP PIPE p", "drop", None),
    ("DROP PIPE IF EXISTS sales.p", "drop", None),
    ("SHOW PIPES", "show", None),
    ("SHOW PIPES FROM sales WHERE NAME = 'p' AND STATE = 'RUNNING' ORDER BY PIPE_NAME LIMIT 5",
     "show", None),
    ("SHOW PIPES LIKE 'ord%'", "show", None),
    ("DESC PIPE p", "describe", None),
    ("DESCRIBE PIPE sales.p", "describe", None),
])
def test_every_pipe_statement_plans_to_a_nova_action(sql, operation, action):
    statement, execution = plan(sql)
    assert isinstance(statement, ast.PipeStatement)
    assert isinstance(execution, NovaActionPlan) and execution.action == "pipe"
    assert execution.payload.operation == operation
    assert execution.payload.action == action
    # Only DROP asks for confirmation, as DROP TASK does.
    assert execution.requires_confirmation == (operation == "drop")


def test_show_pipes_keeps_its_clauses_as_written():
    _, execution = plan(
        "SHOW PIPES FROM sales WHERE NAME = 'p' ORDER BY PIPE_NAME DESC LIMIT 2")
    payload = execution.payload
    assert payload.show_database == "sales"
    assert payload.show_where == "NAME = 'p'"
    assert payload.show_order == "PIPE_NAME DESC" and payload.show_limit == "LIMIT 2"


def test_create_decodes_target_source_and_properties():
    _, execution = plan(CREATE)
    definition = execution.payload.definition
    assert definition.name == ("sales", "orders_pipe") and definition.target == ("orders",)
    assert definition.source.form == "files" and definition.source.stage_name == "landing"
    assert definition.source.path == ("orders",) and definition.source.pattern == "*.parquet"
    assert definition.properties == PipeProperties(True, 10, 1024**2, 2)
    shorthand = decode_create(parse_statement(
        "CREATE PIPE p AS INSERT INTO t SELECT * FROM @landing/x/*.csv"), "sales")
    assert shorthand.source.form == "stage" and shorthand.source.format == "csv"
    assert shorthand.properties == PipeProperties()


@pytest.mark.parametrize(("sql", "message"), [
    ("CREATE PIPE p AS INSERT OVERWRITE t SELECT * FROM @s/a.csv", "INSERT OVERWRITE"),
    ("CREATE PIPE p AS INSERT INTO t WITH LABEL l1 SELECT * FROM @s/a.csv", "WITH LABEL"),
    ("CREATE PIPE p AS INSERT INTO t VALUES (1)", "SELECT"),
    ('CREATE PIPE p AS INSERT INTO t SELECT * FROM FILES("path" = "s3://b/a.csv", '
     '"format" = "csv")', "from a stage"),
    ('CREATE PIPE p AS INSERT INTO t SELECT * FROM FILES("path" = "@s/a.csv", '
     '"format" = "csv", "aws.s3.secret_key" = "x")', "credentials"),
    ('CREATE PIPE p PROPERTIES ("SPEED" = "1") AS INSERT INTO t SELECT * FROM @s/a.csv',
     "Unknown pipe property"),
    ('CREATE PIPE p PROPERTIES ("AUTO_INGEST" = "MAYBE") AS INSERT INTO t '
     "SELECT * FROM @s/a.csv", "AUTO_INGEST"),
    ('CREATE PIPE p PROPERTIES ("POLL_INTERVAL" = "0") AS INSERT INTO t '
     "SELECT * FROM @s/a.csv", "POLL_INTERVAL"),
    ("CREATE PIPE p AS INSERT INTO t SELECT * FROM @s/a.csv JOIN @s/b.csv", "exactly one"),
    ("CREATE PIPE p AS INSERT INTO t SELECT * FROM @s/folder/", "extension"),
    ('CREATE PIPE p AS INSERT INTO t SELECT * FROM FILES("path" = "@s/a.xml", '
     '"format" = "xml")', "not supported"),
    ("CREATE OR REPLACE PIPE IF NOT EXISTS p AS INSERT INTO t SELECT * FROM @s/a.csv",
     "either OR REPLACE"),
    ("CREATE PIPE a.b.c AS INSERT INTO t SELECT * FROM @s/a.csv", "at most two parts"),
])
def test_invalid_pipes_are_refused_before_anything_runs(sql, message):
    with pytest.raises(PipeDefinitionError, match=message):
        plan(sql)


def test_a_pipe_needs_a_database():
    with pytest.raises(PipeDefinitionError, match="Select a database"):
        plan("CREATE PIPE p AS INSERT INTO t SELECT * FROM @s/a.csv", database=None)
    with pytest.raises(PipeDefinitionError, match="SHOW PIPES FROM"):
        plan("SHOW PIPES", database=None)


def test_alter_set_refuses_an_unknown_property():
    with pytest.raises(PipeDefinitionError, match="Unknown pipe property"):
        plan('ALTER PIPE p SET ("FAST" = "TRUE")')


@pytest.mark.parametrize("sql", [
    CREATE, "ALTER PIPE p RESUME", "ALTER PIPE p SUSPEND", "DROP PIPE p", "SHOW PIPES",
    "DESC PIPE p",
])
def test_a_pipe_statement_is_never_forwarded_to_the_engine(sql):
    """Even as a NativeStatement (registration missing) the engine never sees it."""
    native = ast.NativeStatement(parse_statement(sql))
    with pytest.raises(CapabilityUnsupportedError, match="not created in the engine"):
        semantics_registry.validate(native, PlanningContext(database="sales"))


def test_plans_carry_no_credentials():
    _, execution = plan(CREATE)
    assert SECRET not in repr(execution) and "secret" not in execution.payload.definition.insert_sql


# ── properties, globs, batches, labels ───────────────────────────────────


def test_sizes_and_property_updates():
    assert parse_size("1GB") == 1024**3 and parse_size("512mb") == 512 * 1024**2
    assert parse_size("2048") == 2048
    with pytest.raises(PipeDefinitionError):
        parse_size("lots")
    base = PipeProperties(True, 300, 1024, 5)
    updated = parse_properties([("poll_interval", "15")], base)
    assert updated == PipeProperties(True, 15, 1024, 5)
    with pytest.raises(PipeDefinitionError, match="twice"):
        parse_properties([("BATCH_FILES", "1"), ("batch_files", "2")])
    assert updated.as_text()["BATCH_SIZE"] == "1KB"


def test_globs_stay_inside_their_folder():
    pattern = glob_regex(("orders", "2026-*", "*.csv"))
    assert pattern.fullmatch("orders/2026-11/a.csv")
    assert not pattern.fullmatch("orders/2026-11/deep/a.csv")
    assert not pattern.fullmatch("orders/2026-11/a.parquet")
    assert glob_regex(("d", "part-[0-9].csv")).fullmatch("d/part-3.csv")
    assert glob_regex(("d", "?.csv")).fullmatch("d/a.csv")
    assert list_prefix(("orders", "2026-*", "*.csv")) == "orders/"
    assert list_prefix(("orders", "a.csv")) == "orders/"
    assert list_prefix(("*.csv",)) == ""


def test_batches_close_at_the_file_count_or_before_the_size():
    files = [PipeFile(f"f{i}", "v", size) for i, size in enumerate([40, 40, 40, 100, 5])]
    grouped = batches(files, max_files=2, max_bytes=90)
    assert [[f.name for f in batch] for batch in grouped] == [
        ["f0", "f1"], ["f2"], ["f3"], ["f4"]]
    assert batches([], max_files=2, max_bytes=10) == []


def test_labels_are_deterministic_per_pipe_and_file_versions():
    pipe = uuid4()
    one = [PipeFile("a.csv", "e1", 1), PipeFile("b.csv", "e2", 1)]
    assert batch_label(pipe, one) == batch_label(pipe, list(reversed(one)))
    assert batch_label(pipe, one) != batch_label(uuid4(), one)
    assert batch_label(pipe, one) != batch_label(pipe, [PipeFile("a.csv", "e9", 1), one[1]])
    label = batch_label(pipe, one)
    assert label.startswith("nova_pipe_") and len(label) <= 128 and label.isidentifier()


def test_a_batch_replaces_the_source_and_names_its_label():
    definition = decode_create(parse_statement(CREATE), "sales")
    sql = render_batch(definition.insert_sql, "nova_pipe_x", ["FILES('path'='s3://b/1')",
                                                             "FILES('path'='s3://b/2')"])
    assert sql.startswith("INSERT INTO orders WITH LABEL nova_pipe_x  (id, name)")
    assert ("FROM (SELECT * FROM FILES('path'='s3://b/1') UNION ALL "
            "SELECT * FROM FILES('path'='s3://b/2')) AS `__nova_pipe_files`") in sql
    shorthand = decode_create(parse_statement(
        "CREATE PIPE p AS INSERT INTO t SELECT * FROM @landing/x/*.csv AS f"), "s")
    assert render_batch(shorthand.insert_sql, "l1", ["FILES('path'='s3://b/1')"]).endswith(
        "FROM (SELECT * FROM FILES('path'='s3://b/1')) AS f")


def test_engine_errors_are_classified():
    from asyncmy.errors import OperationalError, ProgrammingError

    assert classify(ProgrammingError(1064, "fail to begin transaction. Label [x] has already "
                                           "been used.")).kind == "duplicate_label"
    assert classify(ProgrammingError(1227, "Access denied; you need INSERT")).kind == "access"
    assert classify(ProgrammingError(1064, "Insert has filtered data")).kind == "data"
    assert classify(OperationalError(2013, "Lost connection")).kind == "unknown"
    assert classify(TimeoutError()).kind == "unknown"
    text = safe_message(f"read 's3://bucket/pfx/a.csv' 'aws.s3.secret_key'='{SECRET}'",
                        {"s3://bucket/pfx/": "@db.db.landing/"})
    assert SECRET not in text and "@db.db.landing/a.csv" in text and "s3://" not in text


# ── the worker's run ─────────────────────────────────────────────────────


class Ledger:
    """The PG ledger's rules, in memory: lease fencing and file transitions."""

    def __init__(self, pipe):
        self.pipe = pipe
        self.files: dict[tuple[str, str], dict] = {}
        self.batches: dict[str, dict] = {}
        self.suspended_tasks: list[str] = []

    def check(self, token, generation):
        if self.pipe["claim_token"] != token or self.pipe["claim_generation"] != generation:
            raise LeaseLost("lost")
        return dict(self.pipe)

    async def claim(self, pipe_id, token, *, seconds):
        if self.pipe["claim_token"] is not None:
            return None
        self.pipe.update(claim_token=token, claim_generation=self.pipe["claim_generation"] + 1)
        return dict(self.pipe)

    async def fence(self, pipe_id, token, generation):
        return self.check(token, generation)

    async def renew(self, pipe_id, token, generation, *, seconds):
        self.check(token, generation)

    async def release(self, pipe_id, token, generation):
        if self.pipe["claim_token"] == token:
            self.pipe["claim_token"] = None

    async def set_state(self, pipe_id, token, generation, *, state, last_error):
        self.check(token, generation)
        self.pipe.update(state=state, last_error=last_error)
        return dict(self.pipe)

    async def suspend_schedule(self, pipe_id, token, generation, task_id):
        self.check(token, generation)
        self.suspended_tasks.append(task_id)

    async def mark_polled(self, pipe_id, token, generation):
        self.check(token, generation)
        self.pipe["last_polled_at"] = datetime.now(UTC)

    async def stage_files(self, pipe_id, token, generation, files):
        self.check(token, generation)
        added = 0
        for name, version, size, _modified in files:
            if (name, version) not in self.files:
                self.files[(name, version)] = {
                    "file_name": name, "file_version": version, "file_size": size,
                    "load_state": "UNLOADED", "batch_label": None, "rows_loaded": None,
                    "error_msg": None, "staged_time": len(self.files),
                }
                added += 1
        return added

    async def files_in(self, pipe_id, state):
        return sorted((dict(f) for f in self.files.values() if f["load_state"] == state),
                      key=lambda f: (f["staged_time"], f["file_name"]))

    async def begin_batch(self, pipe_id, token, generation, label, files, total):
        self.check(token, generation)
        assert self.batches.get(label, {}).get("state") != "FINISHED"
        self.batches[label] = {"state": "LOADING", "files": list(files)}
        for key in files:
            assert self.files[key]["load_state"] == "UNLOADED"
            self.files[key].update(load_state="LOADING", batch_label=label)

    async def finish_batch(self, pipe_id, token, generation, label, rows):
        self.check(token, generation)
        self.batches[label]["state"] = "FINISHED"
        self.batches[label]["rows"] = sum(v or 0 for v in rows.values())
        for key, count in rows.items():
            assert self.files[key]["load_state"] == "LOADING"
            self.files[key].update(load_state="FINISHED", rows_loaded=count)

    async def fail_batch(self, pipe_id, token, generation, label, *, error, file_state):
        self.check(token, generation)
        self.batches[label]["state"] = "FAILED"
        for item in self.files.values():
            if item["batch_label"] == label and item["load_state"] == "LOADING":
                item.update(load_state=file_state,
                            error_msg=error if file_state == "ERROR" else None,
                            batch_label=label if file_state == "ERROR" else None)

    def state(self, name):
        return next(f["load_state"] for f in self.files.values() if f["file_name"] == name)


@dataclass
class Config:
    bucket: str = "nova-stages"
    base_prefix: str = "sales/sales/landing"
    access_key: str = "AKIAEXAMPLE"
    secret_key: str = SECRET
    endpoint: str = "http://minio:9000"
    region: str = "us-east-1"
    storage_connection: str = "production"


class Engine:
    """Commits labels like StarRocks: a FINISHED label is refused again."""

    def __init__(self, bad=(), refuse=False, lose=()):
        self.labels: dict[str, int] = {}
        self.statements: list[str] = []
        self.bad = set(bad)
        self.refuse = refuse
        self.lose = set(lose)

    async def insert(self, sql):
        self.statements.append(sql)
        label = sql.split("WITH LABEL ", 1)[1].split()[0]
        if self.refuse:
            raise EngineFailure("access", "Access denied; you need the INSERT privilege")
        if label in self.labels:
            raise EngineFailure("duplicate_label", f"Label [{label}] has already been used.")
        broken = [name for name in self.bad if name in sql]
        if broken:
            raise EngineFailure("data", f"Insert has filtered data in s3://nova-stages/"
                                        f"sales/sales/landing/{broken[0]}")
        rows = sql.count("FILES(") * 10
        self.labels[label] = rows
        if label in self.lose:
            self.lose.discard(label)
            raise EngineFailure("unknown", "Lost connection to the engine")
        return rows

    async def label_state(self, database, label):
        if label in self.labels:
            return LabelState(finished=True, rows=self.labels[label])
        return LabelState()


def make_run(monkeypatch, *, listing, engine, access=None, stage_ok=True, auto_ingest=True,
             form="files", fmt="parquet"):
    pipe_id = uuid4()
    pipe = {
        "id": pipe_id, "state": "RUNNING", "owner_role": "LOADER", "wake_count": 0,
        "claim_token": None, "claim_generation": 0, "last_error": None, "task_id": "task-1",
        "target_table": "sales.orders", "auto_ingest": auto_ingest, "last_polled_at": None,
        "batch_files": 2, "batch_size": 10**9,
        "definition": decode_create(parse_statement(CREATE), "sales").insert_sql,
        "source": {"form": form, "stage_name": "landing", "path": ["orders"],
                   "pattern": "*." + fmt, "format": fmt, "options": [],
                   "stage_id": "stage-1", "stage_label": "@sales.sales.landing",
                   "segments": ["orders", "*." + fmt], "session_database": "sales"},
    }
    ledger = Ledger(pipe)

    @asynccontextmanager
    async def fake_ledger():
        yield ledger

    monkeypatch.setattr(runner_module, "_ledger", fake_ledger)

    async def table_access(owner, role, database, table):
        return access

    async def authorize_stage(owner, role, stage_id):
        if not stage_ok:
            from app.modules.stages.access import StageAccessDenied

            raise StageAccessDenied("denied")
        return {"id": stage_id}

    async def list_files(config, segments):
        return list(listing)

    async def csv_layout(config, relative):
        return {"csv.column_separator": ","}, ["id", "name"]

    @asynccontextmanager
    async def open_engine(owner, role, database):
        yield engine

    runtime = PipeRuntime(
        table_access=table_access, authorize_stage=authorize_stage,
        storage_config=lambda stage: Config(), list_files=list_files, csv_layout=csv_layout,
        engine=open_engine,
    )
    return PipeRun(pipe_id, "svc_loader", "LOADER", runtime), ledger


def stamp():
    return datetime(2026, 11, 16, tzinfo=UTC)


def test_new_files_load_once_in_labelled_batches(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    listing = [(f"orders/{n}.parquet", f"e{n}", 10, stamp()) for n in "abc"]
    engine = Engine()
    run, ledger = make_run(monkeypatch, listing=listing, engine=engine)
    summary = asyncio.run(run.run())
    assert "3 new file(s) found; 3 file(s) loaded in 2 batch(es)" in summary
    assert len(engine.statements) == 2 and all(SECRET in sql for sql in engine.statements)
    assert {f["load_state"] for f in ledger.files.values()} == {"FINISHED"}
    # The next poll finds nothing new and runs nothing.
    run2, _ = PipeRun(run.pipe_id, "svc_loader", "LOADER", run.runtime), None
    assert "0 new file(s) found; 0 file(s) loaded" in asyncio.run(run2.run())
    assert len(engine.statements) == 2
    # Credentials reach only the engine: never the ledger, summary or logs.
    assert SECRET not in repr(ledger.files) + repr(ledger.batches) + repr(ledger.pipe)
    assert SECRET not in summary and SECRET not in caplog.text


def test_a_changed_file_version_loads_again_but_the_same_version_never(monkeypatch):
    engine = Engine()
    listing = [("orders/a.parquet", "e1", 10, stamp())]
    run, ledger = make_run(monkeypatch, listing=listing, engine=engine)
    asyncio.run(run.run())
    listing.append(("orders/a.parquet", "e2", 12, stamp()))
    asyncio.run(PipeRun(run.pipe_id, "svc_loader", "LOADER", run.runtime).run())
    assert len(engine.statements) == 2
    assert sorted((k[1], f["load_state"]) for k, f in ledger.files.items()) == [
        ("e1", "FINISHED"), ("e2", "FINISHED")]


def test_a_lost_outcome_is_settled_by_its_label_not_replayed(monkeypatch):
    listing = [("orders/a.parquet", "e1", 10, stamp())]
    label = None
    engine = Engine()
    run, ledger = make_run(monkeypatch, listing=listing, engine=engine)
    label = batch_label(run.pipe_id, [PipeFile("orders/a.parquet", "e1", 10)])
    engine.lose.add(label)
    summary = asyncio.run(run.run())
    assert "the next run settles it by its label" in summary
    assert ledger.state("orders/a.parquet") == "LOADING" and engine.labels == {label: 10}
    # The next run asks the engine about the label and records the load.
    asyncio.run(PipeRun(run.pipe_id, "svc_loader", "LOADER", run.runtime).run())
    assert ledger.state("orders/a.parquet") == "FINISHED"
    assert len(engine.statements) == 1


def test_a_lost_submission_is_sent_again_under_the_same_label(monkeypatch):
    """Intent recorded, statement never reached the engine: the same label again."""
    engine = Engine()
    run, ledger = make_run(monkeypatch, listing=[("orders/a.parquet", "e1", 10, stamp())],
                           engine=engine)
    asyncio.run(ledger.claim(run.pipe_id, "t", seconds=1))
    asyncio.run(ledger.stage_files(run.pipe_id, "t", 1, [("orders/a.parquet", "e1", 10, None)]))
    label = batch_label(run.pipe_id, [PipeFile("orders/a.parquet", "e1", 10)])
    asyncio.run(ledger.begin_batch(run.pipe_id, "t", 1, label, [("orders/a.parquet", "e1")], 10))
    ledger.pipe["claim_token"] = None  # the earlier run's lease expired
    asyncio.run(run.run())
    assert ledger.state("orders/a.parquet") == "FINISHED"
    assert [s.split("WITH LABEL ")[1].split()[0] for s in engine.statements] == [label]


def test_a_duplicate_label_counts_as_loaded_after_checking(monkeypatch):
    engine = Engine()
    run, ledger = make_run(monkeypatch, listing=[("orders/a.parquet", "e1", 10, stamp())],
                           engine=engine)
    label = batch_label(run.pipe_id, [PipeFile("orders/a.parquet", "e1", 10)])
    engine.labels[label] = 7  # committed by an earlier attempt whose receipt was lost
    asyncio.run(run.run())
    assert ledger.state("orders/a.parquet") == "FINISHED"
    assert ledger.batches[label]["rows"] == 7


def test_a_bad_file_fails_alone_and_retry_loads_it(monkeypatch):
    listing = [(f"orders/{n}.parquet", f"e{n}", 10, stamp()) for n in "ab"]
    engine = Engine(bad={"orders/b.parquet"})
    run, ledger = make_run(monkeypatch, listing=listing, engine=engine)
    summary = asyncio.run(run.run())
    assert "1 file(s) failed" in summary
    assert ledger.state("orders/a.parquet") == "FINISHED"
    assert ledger.state("orders/b.parquet") == "ERROR"
    bad = ledger.files[("orders/b.parquet", "eb")]
    assert "@sales.sales.landing/orders/b.parquet" in bad["error_msg"]
    assert "s3://" not in bad["error_msg"] and "orders/b.parquet" in ledger.pipe["last_error"]
    # Fixed in place under the same version: RETRY FILE queues it, the next run loads it.
    engine.bad.clear()
    bad.update(load_state="UNLOADED", error_msg=None, batch_label=None)
    asyncio.run(PipeRun(run.pipe_id, "svc_loader", "LOADER", run.runtime).run())
    assert ledger.state("orders/b.parquet") == "FINISHED"


@pytest.mark.parametrize(("access", "stage_ok", "reason"), [
    ("The role LOADER can no longer insert into sales.orders", True, "insert into"),
    (None, False, "can no longer read the stage"),
])
def test_revoked_access_suspends_the_pipe_and_loads_nothing(monkeypatch, access, stage_ok,
                                                            reason):
    engine = Engine()
    run, ledger = make_run(monkeypatch, listing=[("orders/a.parquet", "e1", 10, stamp())],
                           engine=engine, access=access, stage_ok=stage_ok)
    summary = asyncio.run(run.run())
    assert "suspended" in summary and engine.statements == [] and ledger.files == {}
    assert ledger.pipe["state"] == "SUSPENDED" and reason in ledger.pipe["last_error"]
    assert ledger.suspended_tasks == ["task-1"]


def test_an_engine_refusal_suspends_and_requeues_the_batch(monkeypatch):
    engine = Engine(refuse=True)
    run, ledger = make_run(monkeypatch, listing=[("orders/a.parquet", "e1", 10, stamp())],
                           engine=engine)
    asyncio.run(run.run())
    assert ledger.pipe["state"] == "SUSPENDED" and ledger.state("orders/a.parquet") == "UNLOADED"


def test_a_suspended_pipe_runs_nothing(monkeypatch):
    engine = Engine()
    run, ledger = make_run(monkeypatch, listing=[("orders/a.parquet", "e1", 10, stamp())],
                           engine=engine)
    ledger.pipe["state"] = "SUSPENDED"
    assert "SUSPENDED" in asyncio.run(run.run()) and engine.statements == []


def test_a_held_lease_keeps_a_second_run_out(monkeypatch):
    engine = Engine()
    run, ledger = make_run(monkeypatch, listing=[("orders/a.parquet", "e1", 10, stamp())],
                           engine=engine)
    ledger.pipe["claim_token"] = uuid4()
    assert asyncio.run(run.run()) == "Another run is loading this pipe"
    assert engine.statements == []


def test_without_auto_ingest_the_pipe_lists_once_and_finishes(monkeypatch):
    listing = [("orders/a.parquet", "e1", 10, stamp())]
    engine = Engine()
    run, ledger = make_run(monkeypatch, listing=listing, engine=engine, auto_ingest=False)
    asyncio.run(run.run())
    assert ledger.pipe["state"] == "FINISHED"
    listing.append(("orders/b.parquet", "e2", 10, stamp()))
    ledger.pipe["state"] = "RUNNING"
    asyncio.run(PipeRun(run.pipe_id, "svc_loader", "LOADER", run.runtime).run())
    assert list(ledger.files) == [("orders/a.parquet", "e1")]


def test_the_shorthand_csv_form_reads_named_header_columns(monkeypatch):
    engine = Engine()
    run, ledger = make_run(monkeypatch, listing=[("orders/a.csv", "e1", 10, stamp())],
                           engine=engine, form="stage", fmt="csv")
    asyncio.run(run.run())
    assert "`$1` AS `id`, `$2` AS `name`" in engine.statements[0]
    assert "'csv.column_separator'=','" in engine.statements[0]


def test_a_replaced_file_version_is_not_read_under_its_old_version(monkeypatch):
    """Retrying a version that was replaced would load the new content twice."""
    listing = [("orders/a.parquet", "e1", 10, stamp())]
    engine = Engine(bad={"orders/a.parquet"})
    run, ledger = make_run(monkeypatch, listing=listing, engine=engine)
    asyncio.run(run.run())
    assert ledger.state("orders/a.parquet") == "ERROR"
    engine.bad.clear()
    listing[0] = ("orders/a.parquet", "e2", 11, stamp())  # corrected copy uploaded
    ledger.files[("orders/a.parquet", "e1")].update(load_state="UNLOADED", error_msg=None,
                                                    batch_label=None)  # RETRY FILE
    asyncio.run(PipeRun(run.pipe_id, "svc_loader", "LOADER", run.runtime).run())
    old, new = ledger.files[("orders/a.parquet", "e1")], ledger.files[("orders/a.parquet", "e2")]
    assert new["load_state"] == "FINISHED"
    assert old["load_state"] == "ERROR" and "no longer in the stage" in old["error_msg"]
    assert len(engine.labels) == 1


def test_engine_failures_can_be_raised_from_a_cause():
    """The real engine path raises ``classify(exc) from None``."""
    from asyncmy.errors import ProgrammingError

    class Cursor:
        rowcount = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, sql):
            raise ProgrammingError(1064, "Insert has filtered data, tracking sql = ...")

    class Connection:
        def cursor(self):
            return Cursor()

    engine = runner_module.OwnerEngine(Connection(), 5)
    with pytest.raises(EngineFailure) as raised:
        asyncio.run(engine.insert("INSERT INTO t WITH LABEL l SELECT 1"))
    assert raised.value.kind == "data" and "filtered data" in raised.value.message


def test_a_cancelled_single_file_batch_is_an_error_not_a_retry_loop(monkeypatch):
    engine = Engine()
    run, ledger = make_run(monkeypatch, listing=[("orders/a.parquet", "e1", 10, stamp())],
                           engine=engine)
    label = batch_label(run.pipe_id, [PipeFile("orders/a.parquet", "e1", 10)])

    async def cancelled(database, asked):
        return LabelState(cancelled=True, error="type:LOAD_RUN_FAIL; msg:Cancelled")

    engine.label_state = cancelled
    asyncio.run(ledger.claim(run.pipe_id, "t", seconds=1))
    asyncio.run(ledger.stage_files(run.pipe_id, "t", 1, [("orders/a.parquet", "e1", 10, None)]))
    asyncio.run(ledger.begin_batch(run.pipe_id, "t", 1, label, [("orders/a.parquet", "e1")], 10))
    ledger.pipe["claim_token"] = None
    asyncio.run(run.run())
    assert ledger.state("orders/a.parquet") == "ERROR" and engine.statements == []
