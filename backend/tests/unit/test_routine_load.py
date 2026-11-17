"""Routine Load through Nova Kafka connections: planning, refusals, the service's
owner and connection checks, secret handling, and the reconciler's pause.

The PostgreSQL store, Ranger and the engine are faked here; the PG rules are in
``tests/integration/test_metadata_routine_loads.py`` and the live run is in
``docs/reports/starrocks-compat/w8-routine-load-live.json``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest

from app.core.exceptions import StarRocksError
from app.modules.access_control.security_context import SecurityContext
from app.modules.routine_load import reconciler, service
from app.modules.routine_load.service import (
    CreateRequest,
    EngineReply,
    RoutineLoadError,
    routine_load_service,
)
from app.sql_frontend.ast.builder import ast_builders
from app.sql_frontend.context import PlanningContext
from app.sql_frontend.errors import CapabilityUnsupportedError, SemanticError
from app.sql_frontend.parser import parse_statement
from app.sql_frontend.planning.execution import ActionKind
from app.sql_frontend.planning.planner import SQLPlanner
from app.sql_frontend.routine_load import RoutineLoadPayload

SASL_SECRET = "k@fka-S3cret"
CREATE = (
    'CREATE ROUTINE LOAD shop.orders_job ON orders COLUMNS TERMINATED BY ",", '
    'COLUMNS (id, customer, amount) PROPERTIES ("format" = "csv", "max_batch_interval" = "5") '
    'FROM KAFKA ("kafka_connection" = "events", "kafka_topic" = "orders", '
    '"property.kafka_default_offsets" = "OFFSET_BEGINNING")'
)


async def plan(sql: str, database: str | None = "shop"):
    statement = ast_builders.build(parse_statement(sql))
    return await SQLPlanner().plan(statement, PlanningContext(database=database))


# ── Planning ──────────────────────────────────────────────────────────────


async def test_create_is_planned_as_a_credential_free_nova_action():
    planned = await plan(CREATE)
    payload = planned.payload
    assert planned.action == ActionKind.ROUTINE_LOAD and isinstance(payload, RoutineLoadPayload)
    assert (payload.database, payload.name, payload.table) == ("shop", "orders_job", "orders")
    assert (payload.connection, payload.topic, payload.data_format) == ("events", "orders", "csv")
    assert payload.sql_before.endswith("FROM KAFKA ")
    assert payload.source_properties == (
        '"kafka_topic" = "orders"', '"property.kafka_default_offsets" = "OFFSET_BEGINNING"'
    )
    assert "kafka_connection" not in "".join(payload.source_properties)
    assert not planned.requires_confirmation


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        (CREATE.replace('"kafka_connection" = "events"', '"kafka_broker_list" = "b:9092"'),
         "comes from the Kafka connection"),
        (CREATE.replace('"kafka_topic" = "orders"',
                        '"kafka_topic" = "orders", "kafka_broker_list" = "b:9092"'),
         "kafka_broker_list"),
        (CREATE.replace('"kafka_topic" = "orders"',
                        '"kafka_topic" = "orders", "property.sasl.password" = "x"'),
         "property.sasl.password"),
        (CREATE.replace('"kafka_topic" = "orders"',
                        '"kafka_topic" = "orders", "property.security.protocol" = "SSL"'),
         "property.security.protocol"),
        (CREATE.replace('"kafka_topic" = "orders"',
                        '"kafka_topic" = "orders", "property.ssl.key.password" = "x"'),
         "property.ssl.key.password"),
        (CREATE.replace(', "kafka_topic" = "orders"', ""), "kafka_topic"),
        (CREATE.replace('"kafka_connection" = "events", ', ""), "names a Nova Kafka connection"),
        (CREATE.replace('"format" = "csv"', '"format" = "parquet"'), "csv, json or avro"),
        (CREATE.replace("ON orders", "ON other.orders"), "ON names a table"),
        (CREATE.replace('"events"', '"bad name"'), "letters, digits and underscores"),
        ("ALTER ROUTINE LOAD FOR shop.j FROM KAFKA (\"kafka_broker_list\" = \"x:1\")",
         "comes from the Kafka connection"),
        ("ALTER ROUTINE LOAD FOR shop.j FROM KAFKA (\"kafka_topic\" = \"x\")",
         "cannot change 'kafka_topic'"),
        ("ALTER ROUTINE LOAD FOR shop.j FROM KAFKA (\"kafka_connection\" = \"x\")",
         "cannot change 'kafka_connection'"),
    ],
)
async def test_broker_secrets_never_reach_sql_text(sql, message):
    with pytest.raises(SemanticError, match=message):
        await plan(sql)


async def test_only_kafka_and_a_named_database():
    with pytest.raises(CapabilityUnsupportedError, match="Kafka"):
        await plan(CREATE.replace("FROM KAFKA", "FROM PULSAR"))
    with pytest.raises(SemanticError, match="database"):
        await plan("PAUSE ROUTINE LOAD FOR j", database=None)


@pytest.mark.parametrize(
    ("sql", "operation", "confirm"),
    [("PAUSE ROUTINE LOAD FOR j", "pause", False),
     ("RESUME ROUTINE LOAD FOR shop.j", "resume", False),
     ("STOP ROUTINE LOAD FOR shop.j", "stop", True),
     ('ALTER ROUTINE LOAD FOR shop.j PROPERTIES ("desired_concurrent_number" = "2")',
      "alter", False)],
)
async def test_lifecycle_statements_are_nova_actions(sql, operation, confirm):
    planned = await plan(sql)
    assert planned.payload.operation == operation and planned.payload.database == "shop"
    assert planned.payload.sql == parse_statement(sql).normalized_sql
    # STOP cannot be undone: it is confirmed like a drop.
    assert planned.requires_confirmation is confirm


async def test_showing_jobs_stays_native():
    for sql in ("SHOW ROUTINE LOAD FROM shop", "SHOW ROUTINE LOAD TASK FROM shop",
                "SHOW ALL ROUTINE LOAD FOR shop.j"):
        assert type(await plan(sql)).__name__ == "EngineSqlPlan"


# ── Service ───────────────────────────────────────────────────────────────

ALICE = SecurityContext("alice", "marketing", security_context_version=4, session_id="s1")
BOB = SecurityContext("bob", "finance", security_context_version=2, session_id="s2")
ADMIN = SecurityContext("nova_admin", "ACCOUNTADMIN", session_id="s3")


class FakeRepo:
    def __init__(self, store):
        self.store = store
        self.connection = None
        self.actor = None

    async def connection_roles(self, name):
        return list(self.store.roles.get(name, []))

    async def live_job(self, database, name):
        for job in self.store.jobs.values():
            if (job["database_name"], job["job_name"]) == (database, name) and job[
                    "state"] in {"CREATING", "UNKNOWN", "ACTIVE", "PAUSED_BY_NOVA"}:
                return dict(job)
        return None

    async def latest_job(self, database, name):
        rows = [job for job in self.store.jobs.values()
                if (job["database_name"], job["job_name"]) == (database, name)]
        return dict(rows[-1]) if rows else None

    async def live_jobs(self):
        return [dict(job) for job in self.store.jobs.values()
                if job["state"] in {"CREATING", "UNKNOWN", "ACTIVE", "PAUSED_BY_NOVA"}]

    async def create_job(self, data):
        job = {**data, "state": "CREATING", "revision": 1, "engine_job_id": None,
               "pause_reason": None, "last_check_error": None,
               "created_at": datetime.now(UTC)}
        self.store.jobs[job["id"]] = job
        return dict(job)

    async def update_job(self, job_id, changes, *, expected_revision):
        job = self.store.jobs[job_id]
        assert job["revision"] == expected_revision
        job.update({k: v for k, v in changes.items() if k != "last_checked_at"})
        job["revision"] += 1
        self.store.updates.append((job_id, dict(changes)))
        return dict(job)


class Store:
    def __init__(self):
        self.connections = {"events": {"name": "events", "connector_type": "kafka",
                                       "host": "kafka:9094", "port": 9094,
                                       "options": {"security_protocol": "SASL_PLAINTEXT",
                                                   "sasl_mechanism": "PLAIN"},
                                       "secret_ref": "nova://events"}}
        self.roles = {"events": ["marketing"]}
        self.jobs: dict[str, dict] = {}
        self.updates: list = []
        self.writers = {("alice", "marketing")}
        self.members = {("alice", "marketing"), ("nova_admin", "ACCOUNTADMIN")}
        self.audits: list[dict] = []


@pytest.fixture
def store(monkeypatch):
    store = Store()

    @asynccontextmanager
    async def transaction(actor, *, read_only=False):
        yield FakeRepo(store)

    async def grant_state(repo, name):
        return store.connections.get(name), list(store.roles.get(name, []))

    async def write_access(principal, role, database, table):
        return (principal, role) in store.writers

    async def holds(principal, role):
        return (principal, role) in store.members

    async def audit(**kwargs):
        store.audits.append(kwargs)

    async def stored(connection, repo):
        return ("loader", SASL_SECRET)

    from app.modules.catalog.connections import connection_service

    for module in (service, reconciler):
        monkeypatch.setattr(module, "routine_load_transaction", transaction)
        monkeypatch.setattr(module, "grant_state", grant_state)
    monkeypatch.setattr(service, "table_write_access", write_access)
    monkeypatch.setattr(service, "holds_role", holds)
    monkeypatch.setattr(service, "write_audit_log", audit)
    monkeypatch.setattr(connection_service, "stored_credentials", stored)
    return store


def request(**changes):
    values = dict(
        database="shop", job_name="orders_job", table_name="orders", connection_name="events",
        topic="orders", data_format="csv",
        sql_before="CREATE ROUTINE LOAD shop.orders_job ON orders FROM KAFKA ",
        source_properties=('"kafka_topic" = "orders"',), sql_after="",
    )
    values.update(changes)
    return CreateRequest(**values)


class Engine:
    def __init__(self, fail=None, jobs=None):
        self.statements: list[str] = []
        self.fail = fail
        self.jobs = jobs if jobs is not None else [{"Id": "77", "State": "NEED_SCHEDULE"}]

    async def __call__(self, sql):
        self.statements.append(sql)
        if sql.startswith("SHOW ALL ROUTINE LOAD"):
            return EngineReply(["Id", "State"], [[j["Id"], j["State"]] for j in self.jobs])
        if self.fail is not None:
            raise self.fail
        return EngineReply([], [])


async def test_create_records_intent_then_runs_as_the_caller_with_the_connection(store):
    engine = Engine()
    executed = await routine_load_service.create(request(), ALICE, engine)
    sent = engine.statements[0]
    assert sent.startswith("CREATE ROUTINE LOAD shop.orders_job ON orders FROM KAFKA (")
    assert "'kafka_broker_list' = 'kafka:9094'" in sent
    assert "'property.security.protocol' = 'SASL_PLAINTEXT'" in sent
    assert f"'property.sasl.password' = '{SASL_SECRET}'" in sent
    assert sent.endswith('"kafka_topic" = "orders")')
    # What leaves the service never holds the secret.
    assert SASL_SECRET not in executed
    assert all(SASL_SECRET not in str(entry) for entry in store.audits)
    job = next(iter(store.jobs.values()))
    assert (job["state"], job["engine_job_id"], job["owner_role"],
            job["owner_principal"], job["security_context_version"]) == (
        "ACTIVE", "77", "marketing", "alice", 4)
    assert store.audits[-1]["status"] == "SUCCESS"


@pytest.mark.parametrize(
    ("actor", "prepare", "message"),
    [
        (BOB, None, "may not load from Kafka connection"),
        (ALICE, lambda s: s.writers.clear(), "may not write to shop.orders"),
        (ALICE, lambda s: s.connections.clear(), "does not exist"),
        (ALICE, lambda s: s.connections["events"].update(connector_type="postgres"),
         "does not exist"),
    ],
)
async def test_create_refusals_happen_before_any_secret_or_engine_call(
        store, monkeypatch, actor, prepare, message):
    if prepare:
        prepare(store)
    engine = Engine()

    async def forbidden(*args):
        raise AssertionError("credential resolved before authorization")

    from app.modules.catalog.connections import connection_service

    monkeypatch.setattr(connection_service, "stored_credentials", forbidden)
    with pytest.raises(RoutineLoadError, match=message):
        await routine_load_service.create(request(), actor, engine)
    assert engine.statements == [] and store.jobs == {}
    assert store.audits[-1]["status"] == "DENIED"


async def test_admins_load_from_any_kafka_connection(store):
    store.writers.add(("nova_admin", "ACCOUNTADMIN"))
    store.roles["events"] = []
    await routine_load_service.create(request(), ADMIN, Engine())
    assert next(iter(store.jobs.values()))["owner_role"] == "ACCOUNTADMIN"


async def test_engine_refusal_marks_the_job_failed_and_redacts(store):
    engine = Engine(
        fail=StarRocksError(f"Access denied 'property.sasl.password' = '{SASL_SECRET}'")
    )
    with pytest.raises(RoutineLoadError) as caught:
        await routine_load_service.create(request(), ALICE, engine)
    assert SASL_SECRET not in str(caught.value)
    assert next(iter(store.jobs.values()))["state"] == "FAILED"


async def test_an_unobserved_outcome_is_recorded_unknown_not_replayed(store):
    engine = Engine(fail=ConnectionResetError("lost"))
    with pytest.raises(RoutineLoadError, match="Nova will check"):
        await routine_load_service.create(request(), ALICE, engine)
    assert next(iter(store.jobs.values()))["state"] == "UNKNOWN"
    assert len([s for s in engine.statements if s.startswith("CREATE")]) == 1


async def test_a_live_name_cannot_be_created_twice(store):
    await routine_load_service.create(request(), ALICE, Engine())
    with pytest.raises(RoutineLoadError, match="already exists"):
        await routine_load_service.create(request(), ALICE, Engine())


async def active_job(store):
    await routine_load_service.create(request(), ALICE, Engine())
    return next(iter(store.jobs.values()))


async def test_only_the_owner_role_or_an_admin_controls_a_managed_job(store):
    await active_job(store)
    engine = Engine()
    with pytest.raises(RoutineLoadError, match="belongs to role marketing"):
        await routine_load_service.control("pause", "shop", "orders_job",
                                           "PAUSE ROUTINE LOAD FOR shop.orders_job", BOB, engine)
    assert engine.statements == []
    await routine_load_service.control("pause", "shop", "orders_job",
                                       "PAUSE ROUTINE LOAD FOR shop.orders_job", ALICE, engine)
    await routine_load_service.control("stop", "shop", "orders_job",
                                       "STOP ROUTINE LOAD FOR shop.orders_job", ADMIN, engine)
    assert engine.statements == ["PAUSE ROUTINE LOAD FOR shop.orders_job",
                                 "STOP ROUTINE LOAD FOR shop.orders_job"]
    assert next(iter(store.jobs.values()))["state"] == "STOPPED"
    with pytest.raises(RoutineLoadError, match="is stopped; a stopped job cannot be resumed"):
        await routine_load_service.control("resume", "shop", "orders_job", "RESUME", ALICE, engine)


async def test_resume_and_alter_need_a_job_created_through_nova(store):
    for operation in ("resume", "alter"):
        with pytest.raises(RoutineLoadError, match="not created through Nova"):
            await routine_load_service.control(operation, "shop", "other", "x", ALICE, Engine())
    # Pausing or stopping a job made outside Nova is left to the engine.
    engine = Engine()
    await routine_load_service.control("pause", "shop", "other",
                                       "PAUSE ROUTINE LOAD FOR shop.other", ALICE, engine)
    assert engine.statements == ["PAUSE ROUTINE LOAD FOR shop.other"]


async def test_resume_rechecks_the_owner_and_renews_the_context(store):
    job = await active_job(store)
    store.jobs[job["id"]].update(state="PAUSED_BY_NOVA", pause_reason="x")
    store.writers.clear()
    engine = Engine()
    with pytest.raises(RoutineLoadError, match="cannot resume: Role marketing may no longer write"):
        await routine_load_service.control("resume", "shop", "orders_job", "RESUME", ALICE, engine)
    assert engine.statements == []
    store.writers.add(("alice", "marketing"))
    renewed = SecurityContext("alice", "marketing", security_context_version=9, session_id="s9")
    await routine_load_service.control("resume", "shop", "orders_job", "RESUME", renewed, engine)
    current = store.jobs[job["id"]]
    assert current["state"] == "ACTIVE" and current["security_context_version"] == 9


# ── Reconciler ────────────────────────────────────────────────────────────


async def test_reconciler_pauses_a_job_whose_owner_lost_write_access(store):
    job = await active_job(store)
    engine = Engine(jobs=[{"Id": "77", "State": "RUNNING"}])
    assert await reconciler.reconcile_job(dict(store.jobs[job["id"]]), run=engine) == "ok"
    store.writers.clear()
    outcome = await reconciler.reconcile_job(dict(store.jobs[job["id"]]), run=engine)
    assert outcome == "paused"
    assert engine.statements[-1] == "PAUSE ROUTINE LOAD FOR `shop`.`orders_job`"
    current = store.jobs[job["id"]]
    assert current["state"] == "PAUSED_BY_NOVA"
    assert "may no longer write to shop.orders" in current["pause_reason"]


@pytest.mark.parametrize(
    ("change", "reason"),
    [(lambda s: s.roles.update(events=[]), "may no longer load from connection"),
     (lambda s: s.members.clear(), "no longer has role marketing"),
     (lambda s: s.connections.clear(), "no longer exists")],
)
async def test_reconciler_pauses_for_every_lost_grant(store, change, reason):
    job = await active_job(store)
    change(store)
    engine = Engine(jobs=[{"Id": "77", "State": "NEED_SCHEDULE"}])
    assert await reconciler.reconcile_job(dict(store.jobs[job["id"]]), run=engine) == "paused"
    assert reason in store.jobs[job["id"]]["pause_reason"]


async def test_reconciler_never_pauses_on_an_unanswered_check(store, monkeypatch):
    job = await active_job(store)

    async def down(*args):
        raise TimeoutError

    monkeypatch.setattr(service, "table_write_access", down)
    engine = Engine(jobs=[{"Id": "77", "State": "RUNNING"}])
    assert await reconciler.reconcile_job(dict(store.jobs[job["id"]]), run=engine) == (
        "check_unavailable")
    assert store.jobs[job["id"]]["state"] == "ACTIVE"
    assert not any(s.startswith("PAUSE") for s in engine.statements)

    async def engine_down(sql):
        raise StarRocksError("engine down")

    assert await reconciler.reconcile_job(dict(store.jobs[job["id"]]), run=engine_down) == (
        "engine_unavailable")
    assert store.jobs[job["id"]]["state"] == "ACTIVE"


async def test_reconciler_records_stopped_and_verifies_unknown_jobs(store):
    job = await active_job(store)
    assert await reconciler.reconcile_job(
        dict(store.jobs[job["id"]]), run=Engine(jobs=[{"Id": "77", "State": "STOPPED"}])
    ) == "stopped"
    # A job of the same name created later is not ours.
    store.jobs.clear()
    job = await active_job(store)
    assert await reconciler.reconcile_job(
        dict(store.jobs[job["id"]]), run=Engine(jobs=[{"Id": "99", "State": "RUNNING"}])
    ) == "stopped"
    store.jobs.clear()
    with pytest.raises(RoutineLoadError):
        await routine_load_service.create(request(), ALICE, Engine(fail=ConnectionResetError()))
    unknown = next(iter(store.jobs.values()))
    assert await reconciler.reconcile_job(
        dict(unknown), run=Engine(jobs=[{"Id": "12", "State": "NEED_SCHEDULE"}])
    ) == "verified"
    assert store.jobs[unknown["id"]]["engine_job_id"] == "12"
    store.jobs.clear()
    with pytest.raises(RoutineLoadError):
        await routine_load_service.create(request(), ALICE, Engine(fail=ConnectionResetError()))
    unknown = next(iter(store.jobs.values()))
    assert await reconciler.reconcile_job(dict(unknown), run=Engine(jobs=[])) == "failed"


async def test_a_fresh_create_is_left_to_its_statement(store):
    job = await (FakeRepo(store)).create_job({
        "id": "x", "database_name": "shop", "job_name": "j", "table_name": "t",
        "connection_name": "events", "topic": "t", "data_format": "csv",
        "owner_principal": "alice", "owner_role": "marketing", "security_context_version": 1,
    })
    assert await reconciler.reconcile_job(job, run=Engine(jobs=[])) == "creating"
    store.jobs["x"]["created_at"] = datetime.now(UTC) - timedelta(minutes=5)
    assert await reconciler.reconcile_job(dict(store.jobs["x"]), run=Engine(jobs=[])) == "failed"


async def test_json_paths_with_escaped_quotes_reach_the_engine_as_written():
    sql = (
        'CREATE ROUTINE LOAD shop.json_job ON orders COLUMNS (id, amount) '
        'PROPERTIES ("format" = "json", "jsonpaths" = "[\\"$.id\\",\\"$.amount\\"]") '
        'FROM KAFKA ("kafka_connection" = "events", "kafka_topic" = "orders_json")'
    )
    payload = (await plan(sql)).payload
    assert payload.data_format == "json"
    assert '"jsonpaths" = "[\\"$.id\\",\\"$.amount\\"]"' in payload.sql_before


# ── SHOW output names the connection, never the broker addresses ─────────


def test_show_values_name_the_connection_instead_of_brokers():
    from app.sql_frontend.routine_load import logical_routine_load_text

    brokers = [("kafka:9092", "events_kafka")]
    shown = logical_routine_load_text(
        '{"topic":"t","currentKafkaPartitions":"0","brokerList":"kafka:9092"}', brokers
    )
    assert shown == '{"topic":"t","currentKafkaPartitions":"0","kafkaConnection":"events_kafka"}'
    create = (
        'FROM KAFKA\n(\n"kafka_topic"="t",\n"kafka_broker_list" = "kafka:9092",\n'
        '"property.sasl.username"="svc-loader",\n"property.sasl.password"="******"\n)'
    )
    shown = logical_routine_load_text(create, brokers)
    assert "kafka:9092" not in shown
    assert '"kafka_connection" = "events_kafka"' in shown
    # The sign-in user stays visible (documented); the password is masked.
    assert '"property.sasl.username"="svc-loader"' in shown
    assert '"property.sasl.password"="***"' in shown


def test_unknown_brokers_and_passwords_are_hidden():
    from app.sql_frontend.routine_load import logical_routine_load_text

    shown = logical_routine_load_text(
        '{"brokerList":"10.0.0.5:9093,10.0.0.6:9093"} {"sasl.password":"x","ssl.key.password":"p"}',
        [],
    )
    assert "10.0.0" not in shown and '"x"' not in shown and '"p"' not in shown
    assert '"kafkaConnection":"hidden"' in shown


def test_unrelated_values_are_unchanged():
    from app.sql_frontend.routine_load import logical_routine_load_text

    assert logical_routine_load_text("RUNNING", [("kafka:9092", "k")]) == "RUNNING"


@pytest.mark.parametrize(
    ("sql", "database", "expected"),
    [
        ("SHOW ROUTINE LOAD FOR db.job", None, True),
        ("SHOW ALL ROUTINE LOAD FROM db", None, True),
        ("SHOW CREATE ROUTINE LOAD db.job", None, True),
        ('SHOW ROUTINE LOAD TASK FROM db WHERE JobName = "job"', None, True),
        ("SELECT * FROM information_schema.routine_load_jobs", None, True),
        ("SELECT * FROM routine_load_jobs", "information_schema", True),
        ("SELECT * FROM routine_load_jobs", "sales", False),
        ("SELECT 1", None, False),
    ],
)
def test_routine_load_rows_are_detected(sql, database, expected):
    from types import SimpleNamespace

    from app.sql_frontend.execution.adapters import _shows_routine_loads

    statement = SimpleNamespace(parsed=parse_statement(sql))
    assert _shows_routine_loads(statement, database) is expected


async def test_broker_map_fails_closed_to_hidden(monkeypatch):
    from app.sql_frontend import routine_load as frontend

    @asynccontextmanager
    async def broken(*_args, **_kwargs):
        raise RuntimeError("metadata unavailable")
        yield

    monkeypatch.setattr(
        "app.modules.routine_load.repository.routine_load_transaction", broken
    )
    assert await frontend.broker_connection_map("alice", "marketing") == []
