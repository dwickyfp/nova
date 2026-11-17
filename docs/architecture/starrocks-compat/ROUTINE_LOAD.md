# Routine Load from Kafka in Nova (W8)

Status: implemented on `feat/dataviz-native` (2026-11-16), metadata migration
0057. Plan context: [PLAN.md](PLAN.md) W8; test services: [INFRA.md](INFRA.md).

## Problem

StarRocks Routine Load is a job the engine keeps running in the background,
long after the statement that created it. Three things made Nova refuse it:

1. The job runs without the caller's sign-in, active role or security-context
   version. Ranger authorizes `CREATE ROUTINE LOAD` once, as the creator, and
   never again: a role that loses write access keeps loading.
2. `FROM KAFKA` carries `kafka_broker_list` and, for SASL clusters,
   `property.sasl.password`. Written in SQL, the secret reaches worksheet
   history, audit rows and query text.
3. Any role that may write the table can create a job from any broker the
   engine can reach, including topics with data that role should not read.

## Options considered

| Option | Why not chosen |
| --- | --- |
| Keep refusing; point to `CREATE TASK` polling | No streaming semantics, offsets or exactly-once commit. |
| Nova worker consumes Kafka and inserts as the owner role's task account | A second consumer framework next to the engine's own; duplicates offset management and loses the engine's transactional commit. |
| Admin-only routine loads, no ownership record | Safe but blocks the main use (a team loading its own table); revocation still not enforced. |
| **Engine job created as the caller, connection-scoped by Nova, recorded and reconciled by Nova** | Chosen. The engine keeps doing what it is good at; Nova adds identity, secrets and lifetime. |

## Design

### Kafka connection

A Kafka connection is a Nova connection of type `kafka`
(`app/modules/catalog/sources/kafka.py`, a `CatalogSource` subclass that backs
no catalog: `provides_catalog = False`).

- Endpoint: `host:port[,host:port...][?security_protocol=...&sasl_mechanism=...]`,
  up to 32 brokers. Protocols `PLAINTEXT`, `SSL`, `SASL_PLAINTEXT`, `SASL_SSL`;
  mechanisms `PLAIN`, `SCRAM-SHA-256`, `SCRAM-SHA-512`. No `user@`, no scheme.
- With a SASL protocol the connection credential (Nova's existing encrypted
  `catalog_connection_credentials`, or a secret-store reference) is the SASL
  username and password. `PLAINTEXT` and `SSL` take no credential
  (`credential_source = none`).
- `SSL` uses the engine's trusted certificates. Client certificates and custom
  CA files are not supported (they are file paths on every BE).
- A security admin chooses the roles that may load from it
  (`nova_meta.catalog_connection_roles`, `PUT /api/v1/catalog/connections/{name}/roles`,
  **Roles that can load** on the Connections page). Security-admin roles can
  always load. Dropping a connection with live jobs is refused.

### Statements

All five lifecycle statements are planned by `app/sql_frontend/routine_load.py`
(typed AST, credential-free `RoutineLoadPayload`, `ActionKind.ROUTINE_LOAD`)
and executed by `app/modules/routine_load/service.py`. `SHOW ROUTINE LOAD`,
`SHOW ALL ROUTINE LOAD`, `SHOW ROUTINE LOAD TASK` and `SHOW CREATE ROUTINE LOAD`
stay native: the engine lists the jobs the caller's role may see. Their rows
(and queries of `information_schema.routine_load_jobs`) pass through
`logical_routine_load_text` in the execution adapter: a broker list becomes
the Kafka connection's name (`kafkaConnection` in `DataSourceProperties`,
`"kafka_connection" = ...` in `SHOW CREATE`), an unknown one becomes `hidden`,
and SASL/SSL passwords are masked. Broker addresses are connection details that
only security admins see on the connections page; a role that may load from a
connection does not need them. The connection map is read as the caller; if it
cannot be read, every broker list is shown as `hidden`.

```sql
CREATE ROUTINE LOAD shop.orders_job ON orders
COLUMNS TERMINATED BY ",", COLUMNS (order_id, customer, amount)
PROPERTIES ("format" = "csv", "max_batch_interval" = "5")
FROM KAFKA ("kafka_connection" = "events", "kafka_topic" = "orders",
            "property.kafka_default_offsets" = "OFFSET_BEGINNING");
```

Planning (pure, no I/O):

- `FROM KAFKA` must contain exactly one `kafka_connection` and a `kafka_topic`.
- Refused: `kafka_broker_list`, every `property.security.*`, `property.sasl.*`,
  `property.ssl.*`, `property.bootstrap.servers`, any credential-named key, and
  a schema-registry URL with `user@`. So no broker secret is ever in SQL text.
- `FROM PULSAR` is refused (Kafka only). Job database must be named or
  selected; the table is in the job's database.
- `ALTER` may not change `kafka_connection` or `kafka_topic` (create a new job)
  and follows the same property rules. `STOP` is confirmed like a drop: a
  stopped job cannot be resumed.

### Create, as the caller

1. Read the connection and its roles in one read-only PG transaction.
2. Refuse unless: the connection exists and is Kafka; the active role is
   allowed on it (or is a security admin); no live job of that name exists;
   Ranger gives the active role `INSERT` or `ALL` on the table
   (`AccessControlService.effective_access`, the check stages use). Refusals
   are audited (`ROUTINE_LOAD` / `DENIED`) before any secret is resolved.
3. **Intent first:** insert `routine_load_jobs` row `CREATING` with owner
   principal, owner role, security-context version, connection, table, topic.
4. Resolve the connection credential, append `kafka_broker_list` and the
   security properties to the caller's own properties, and run the statement on
   the caller's engine session with the caller's active role (the same
   `execute_as_user` path as every worksheet statement, including the MySQL
   proxy). The engine and Ranger authorize the write again as the caller.
5. Engine error → `FAILED`. No observed outcome (connection lost, cancel) →
   `UNKNOWN`; it is never retried. Success → read the engine job id and mark
   `ACTIVE`. Audit, history and the API response carry the redacted statement.

The engine masks `property.sasl.password` in `SHOW ROUTINE LOAD` (`******`,
verified on the pinned 4.1.4 image).

### Control

- A job Nova manages is paused, resumed, altered or stopped only by its owner
  role or a security admin; others are refused (even if the engine would allow
  them because they can write the table).
- `RESUME` re-runs the access checks for the **owner** (connection exists, the
  owner role is still allowed on it, the owner principal still holds the owner
  role, the owner role still may write the table). When the owner resumes, the
  recorded security-context version is renewed.
- `RESUME` and `ALTER` of a job not created through Nova are refused; `PAUSE`
  and `STOP` of such a job are left to the engine (it authorizes them as the
  caller), so existing jobs can always be stopped.

### Reconciler

`app/modules/routine_load/reconciler.py` runs in the existing maintenance
worker (`app/worker/maintenance.py`, loop `routine-load`, supervised like the
catalog refresher, one instance per pass by Redis leader lock), every 60 s:

| Found | Action |
| --- | --- |
| `UNKNOWN`, or `CREATING` older than 2 minutes | `ACTIVE` with the engine id if the engine has a live job of that name, else `FAILED`. Nothing is re-created. |
| Engine job missing, `STOPPED`, `CANCELLED`, or a newer job with another id | `STOPPED` |
| Owner lost connection grant, role assignment or table write access | `PAUSE ROUTINE LOAD` (if running), `PAUSED_BY_NOVA` with the reason |
| Ranger or the engine does not answer | Nothing changes; `last_check_error` records it. An outage never pauses or resumes jobs. |

The reconciler reads and pauses through the control plane's system session:
pausing is enforcement, not data access; the job keeps its owner. It never
resumes a job; only `RESUME` by the owner role or an admin does, after the
same checks pass.

### Metadata (0057)

- `nova_meta.catalog_connection_roles(connection_name, role_name, granted_by,
  granted_at)`: read by bound web/query/proxy/maintenance actors; insert and
  delete only by a bound USER actor in a security-admin role. Deleted with its
  connection.
- `nova_meta.routine_load_jobs`: every signed-in USER actor reads which jobs
  Nova manages (so a non-owner's statement is refused, not passed through);
  insert only as yourself for your active role; update only by the owner role
  or administrators; the reconciler (`nova-routine-load` SERVICE actor on the
  maintenance login) reads all and updates state columns. Row guard: identity
  immutable, engine id set once, revision +1 per change, lifecycle
  `CREATING → ACTIVE|UNKNOWN|FAILED`, `UNKNOWN → ACTIVE|FAILED`,
  `ACTIVE ↔ PAUSED_BY_NOVA`, `→ STOPPED` (final); only the owner renews the
  security-context version. One live job per `(database, job)`.
- `catalog_row_guard` accepts connection types `iceberg` and `kafka`.

## Limits

- Kafka only (no Pulsar); SASL `PLAIN`/`SCRAM`; no Kerberos, client
  certificates or custom CA files.
- Revocation takes effect at the next reconciler pass (about a minute); rows
  already loaded stay.
- A job created directly on the engine outside Nova (not through the worksheet
  or Nova's MySQL endpoint) is not reconciled.
- Single development host verified; no multi-instance reconciler fault test.

## Evidence

- Unit: `backend/tests/unit/test_routine_load.py`,
  `backend/tests/unit/test_catalog_stream_and_lake_sources.py`.
- PG17: `backend/tests/integration/test_metadata_routine_loads.py`.
- Live: `docs/reports/starrocks-compat/w8-routine-load-live.json`.
