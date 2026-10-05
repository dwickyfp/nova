---
name: nova-streams
title: Explain and draft Nova Streams SQL
summary: Draft database-scoped Stream SQL and explain its current disabled or provider-unavailable execution status.
triggers: nova streams, create stream, drop stream, change journal, backlog, nova_stream_has_data, buat stream, hapus stream, perubahan tabel
source: docs/sql_docs/13-nova-streams.md
---

# Skill: nova-streams

## Current availability

Nova parses Stream DDL and resolves its namespace, but production execution is
not available. With `STREAMS_ENABLED=false`, commands return Streams are disabled.
Setting it to true still returns Streams provider is unavailable. Neither this
flag nor `MANAGED_APPEND_ENABLED` establishes source readiness. Do not recommend
turning flags on as a way to make these examples work.

Explain the syntax or prepare a draft when requested. Label drafts as not
executable in the current implementation. Never report that a Stream was created,
read, consumed, or dropped without a successful execution result.

## Names and source scope

With `analytics` as the active database, `orders_stream`,
`analytics.orders_stream`, and `analytics.default.orders_stream` identify the
same object. `default` is a case-insensitive UI schema placeholder. Other schemas
and external Stream catalogs are unsupported. Short names require an active
database. Preserve identifier spelling and escape backticks by doubling them.

An unqualified source uses the session database, independently of the Stream's
destination database. Prefer explicit database names when drafting cross-database
statements. Stream names must not collide with tables or views; `IF NOT EXISTS`
does not bypass collision or access checks.

## Draft SQL

These are separate examples with `analytics` as the session database. They show
accepted syntax, not a successful runtime lifecycle.

```sql
CREATE STREAM analytics.default.orders_stream
ON TABLE analytics.default.orders APPEND_ONLY = TRUE;
```

```sql
CREATE STREAM IF NOT EXISTS analytics.orders_stream
ON TABLE analytics.orders APPEND_ONLY = TRUE;
```

```sql
SHOW STREAMS;
```

```sql
DESCRIBE STREAM analytics.orders_stream;
```

```sql
SHOW STREAM STATUS analytics.orders_stream;
```

```sql
SHOW STREAM BACKLOG analytics.orders_stream;
```

```sql
SELECT NOVA_STREAM_HAS_DATA('analytics.default.orders_stream');
```

```sql
DROP STREAM analytics.orders_stream;
```

```sql
DROP STREAM IF EXISTS analytics.orders_stream;
```

## Execution boundaries

DROP is destructive and requires the existing confirmation flow. Confirmation
does not establish runtime availability: confirmed DROP still fails while Streams
is disabled or its provider is unavailable. The MySQL proxy retains its destructive
statement refusal. Loading this skill grants no permission to execute SQL.

For an execution request, explain the current limitation. Do not submit repeated
mutations, replay an uncertain result, write control-plane rows in `NOVA_SYSTEM`,
or send managed Stream syntax directly to StarRocks as a workaround.

`SHOW STREAM LOAD` is a separate native StarRocks statement; it does not list Nova
Stream cursors. Tasks using `NOVA_STREAM_HAS_DATA` are also unavailable. Do not
present a task trigger as an operational workaround.

SELECT/INSERT snapshot execution and cursor consumption are unfinished. Stable
peek, metadata wildcard rules, and transactional consumption belong to the planned
V1 contract, not currently verified query behavior. Do not invent initial-row,
force-advance, production STANDARD, or schema-harmonization support.
