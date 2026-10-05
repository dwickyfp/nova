# Nova Streams SQL: namespace and current availability

Nova accepts Stream syntax through its central grammar, typed AST, and planner.
Production Stream execution is unavailable. Both `STREAMS_ENABLED` and
`MANAGED_APPEND_ENABLED` default to false; enabling the first still returns
provider unavailable. Parser success is not proof of source capture, governance,
or an executed Stream operation.

## Namespace

For session database `analytics`, `s`, `analytics.s`, and `analytics.default.s`
resolve to the same internal-catalog object. `default` is a case-insensitive UI
placeholder, not an independent physical schema. Other schemas and external
Stream catalogs are rejected. Unqualified names need a session database.
Identifier spelling is preserved; quoted identifiers can contain dots and escaped
backticks. An unqualified ON TABLE source uses the session database, independently
of the destination Stream's namespace.

## Recognized statements

| Statement | Current behavior |
| --- | --- |
| `CREATE STREAM [IF NOT EXISTS] name ON TABLE source APPEND_ONLY = TRUE` | Typed action; disabled/provider-unavailable at execution |
| `DROP STREAM [IF EXISTS] name` | Requires destructive confirmation; still unavailable after confirmation |
| `DESCRIBE STREAM name` / `DESC STREAM name` | Typed action; unavailable |
| `SHOW STREAMS` | Requires active database; unavailable |
| `SHOW STREAM STATUS name` | Typed action; unavailable |
| `SHOW STREAM BACKLOG name` | Typed action; unavailable |
| `SELECT NOVA_STREAM_HAS_DATA('database.stream')` | Literal name binding; evaluation unavailable |

`SHOW STREAM LOAD` remains a separate native StarRocks statement. Stream functions
in task definitions cannot bypass the unavailable runtime. No alternative SQL,
direct metadata write, or flag change provides a supported activation path.

## What the tests establish

Parser/planner tests establish namespace, payload, effects, and syntax rejection.
QueryService/proxy tests establish refusals without engine submission and public-SQL
audit. Catalog tests with injected adapters establish create/get/drop/recreate
rules, collision handling, and denied access. These are not successful end-to-end
SQL Stream lifecycle tests.

The immutable metadata namespace gate was tested on isolated StarRocks 4.1.4.
Production provider/governance adapters, SELECT/INSERT snapshot execution,
consumption, and REST/Explorer delivery remain unfinished. Future activation must
satisfy the full Streams V1 acceptance gates.

The packaged Nove playbook is
[`nova-streams.md`](../../backend/app/modules/assistant/skill_library/nova-streams.md).
Its scripted evals prove skill discovery, real-loader delivery, and controlled loop
behavior; they do not measure the decisions of every live language model.
