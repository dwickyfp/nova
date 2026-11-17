---
title: Load continuously from Kafka
description: Connect a Kafka cluster, then keep a table filled from a topic with CREATE ROUTINE LOAD, and watch, pause, change and stop the load.
---

## What it is

**Kafka** is a system that keeps streams of messages, grouped in **topics**.
A **routine load** is a job that keeps reading new messages from one topic and
adds them to a table, every few seconds, until you stop it. Messages can be
CSV lines, JSON objects or Avro records.

Nova runs routine loads in the query engine with three additions:

- The Kafka cluster is a Nova **connection**. You name the connection in SQL;
  broker addresses and passwords never appear in SQL, history or audit.
- The job belongs to the **role** you created it with. Only that role and
  administrators can pause, resume, change or stop it.
- About once a minute Nova checks that the owner role may still write to the
  table and use the connection. If not, Nova pauses the job.

## Before you start

- A security administrator adds the Kafka connection and chooses the roles
  that may load from it.
- Your active role needs `INSERT` on the target table (see
  [Data access](/docs/access-control/data-access)).
- The table must exist in a Nova database. Routine loads cannot write to an
  external catalog.

## Add a Kafka connection

1. Open **Database Explorer**, click the **...** button (**Explorer actions**)
   and choose **Manage connections**.
2. Click **Add connection** and choose **Kafka**.
3. Fill in:
   - **Name**, for example `events_kafka`;
   - **Brokers**: `host:port` of one or more brokers, separated by commas, as
     the query engine reaches them;
   - **Security protocol**: `PLAINTEXT`, `SSL`, `SASL_PLAINTEXT` or
     `SASL_SSL`;
   - for a SASL protocol, **SASL mechanism** (`PLAIN`, `SCRAM-SHA-256` or
     `SCRAM-SHA-512`), **SASL username** and **SASL password**.
4. Click **Add connection**.
5. In the connection's actions menu choose **Roles that can load**, type the
   roles (for example `marketing`), and click **Save roles**.

`PLAINTEXT` and `SSL` connections take no password; the **Credentials** column
shows **Not needed**. `SSL` checks the brokers' certificates against the
engine's trusted certificates. Security administrator roles can always load.

With SQL (a SASL connection then gets its password through **Enter
credentials**, or `SECRET` names a secret-store entry):

```sql
CREATE CONNECTION events_kafka TYPE = kafka ENDPOINT = 'kafka:9092';
DESCRIBE CONNECTION events_kafka;
```

```text
Name          Type   Credentials  Catalogs  Owner       Comment  Created
events_kafka  kafka  none                   nova_admin  NULL     2026-11-16 ...
```

For SASL, the endpoint adds the settings:
`'broker-1:9093,broker-2:9093?security_protocol=SASL_SSL&sasl_mechanism=SCRAM-SHA-512'`.

## Load CSV messages

Each message is one line, for example `1,alice,10.50`.

```sql
CREATE DATABASE kafka_demo;
CREATE TABLE kafka_demo.orders (
  order_id INT,
  customer VARCHAR(32),
  amount DECIMAL(10, 2)
) DUPLICATE KEY(order_id) DISTRIBUTED BY HASH(order_id) BUCKETS 1;

CREATE ROUTINE LOAD kafka_demo.orders_csv ON orders
COLUMNS TERMINATED BY ",",
COLUMNS (order_id, customer, amount)
PROPERTIES (
  "format" = "csv",
  "desired_concurrent_number" = "1",
  "max_batch_interval" = "5",
  "max_error_number" = "0"
)
FROM KAFKA (
  "kafka_connection" = "events_kafka",
  "kafka_topic" = "shop_orders",
  "property.kafka_default_offsets" = "OFFSET_BEGINNING"
);
```

After a few seconds:

```sql
SELECT * FROM kafka_demo.orders ORDER BY order_id;
```

```text
order_id  customer  amount
1         alice     10.50
2         bob       20.00
3         carol     7.25
```

## Load JSON messages

Each message is one JSON object, for example
`{"order_id": 1, "customer": "alice", "amount": 10.5}`. `jsonpaths` picks the
fields in the order of `COLUMNS`:

```sql
CREATE TABLE kafka_demo.orders_json (
  order_id INT,
  customer VARCHAR(32),
  amount DECIMAL(10, 2)
) DUPLICATE KEY(order_id) DISTRIBUTED BY HASH(order_id) BUCKETS 1;

CREATE ROUTINE LOAD kafka_demo.orders_from_json ON orders_json
COLUMNS (order_id, customer, amount)
PROPERTIES (
  "format" = "json",
  "jsonpaths" = "[\"$.order_id\",\"$.customer\",\"$.amount\"]",
  "max_batch_interval" = "5"
)
FROM KAFKA (
  "kafka_connection" = "events_kafka",
  "kafka_topic" = "shop_orders_json",
  "property.kafka_default_offsets" = "OFFSET_BEGINNING"
);
SELECT COUNT(*) FROM kafka_demo.orders_json;
```

Without `jsonpaths`, fields are matched to columns by name.

## Compute and filter columns

`COLUMNS` can compute a column from the message, and `WHERE` keeps only some
messages. `WHERE` uses the table's column names, including computed ones:

```sql
CREATE TABLE kafka_demo.big_orders (
  order_id INT,
  customer VARCHAR(32),
  amount_cents BIGINT
) DUPLICATE KEY(order_id) DISTRIBUTED BY HASH(order_id) BUCKETS 1;

CREATE ROUTINE LOAD kafka_demo.big_orders_csv ON big_orders
COLUMNS TERMINATED BY ",",
COLUMNS (order_id, customer, amount, amount_cents = amount * 100),
WHERE amount_cents >= 1000
PROPERTIES ("format" = "csv", "max_batch_interval" = "5")
FROM KAFKA (
  "kafka_connection" = "events_kafka",
  "kafka_topic" = "shop_orders",
  "property.kafka_default_offsets" = "OFFSET_BEGINNING"
);
SELECT * FROM kafka_demo.big_orders ORDER BY order_id;
```

```text
order_id  customer  amount_cents
1         alice     1050
2         bob       2000
```

## Every option

### What the statement says

| Part | Meaning |
| --- | --- |
| `kafka_demo.orders_csv` | The job's database and name. The name is unique among the database's running and paused jobs. |
| `ON orders` | The table in that database that receives the rows. |
| `COLUMNS TERMINATED BY` | CSV column separator. Default: a tab. Up to 50 bytes. Write a missing value as `\N`. |
| `ROWS TERMINATED BY` | CSV row separator. Default: a new line. |
| `COLUMNS (...)` | The message fields in order, and computed columns (`name = expression`). |
| `WHERE` | Only matching messages are loaded. It names table columns (computed ones included); a message field that is not a table column fails with `the column ... in where clause must be in the target table`. |
| `PARTITION (p1, ...)` | Load only into these partitions; other rows are filtered out. |
| `TEMPORARY PARTITION (...)` | Load into temporary partitions. |

### `PROPERTIES` (the job)

| Property | Default | Meaning |
| --- | --- | --- |
| `format` | `csv` | `csv`, `json` or `avro`. |
| `desired_concurrent_number` | `3` | How many tasks read at once; also limited by the topic's partitions. |
| `max_batch_interval` | `10` | Seconds between tasks, 5 to 60. |
| `max_batch_rows` | `200000` | Sets the error window: 10 times this many rows. |
| `max_error_number` | `0` | Bad rows allowed in one error window before the job pauses. |
| `max_filter_ratio` | `1` | Share (0 to 1) of rows that may be filtered out for bad data. |
| `strict_mode` | `false` | When `true`, a value that cannot be converted makes the row an error instead of `NULL`. |
| `log_rejected_record_num` | `0` | How many rejected rows to keep for inspection (`-1`: all). |
| `timezone` | the session's | Time zone for time functions in `COLUMNS`. |
| `partial_update` | `false` | Update only the listed columns of a primary key table. |
| `merge_condition` | none | Update a row only when this column's new value is at least the stored one. |
| `trim_space` | `false` | Remove spaces around CSV separators. |
| `enclose` | none | CSV quote character, such as `"`. |
| `escape` | none | CSV escape character. |
| `strip_outer_array` | `false` | JSON: a message holding an array of objects loads each object. |
| `jsonpaths` | none | JSON: the fields to load, in `COLUMNS` order. |
| `json_root` | none | JSON: the element whose contents are loaded. |
| `task_consume_second` | engine setting | Seconds each task reads. |
| `task_timeout_second` | engine setting | Seconds before a task times out. |
| `pause_on_fatal_parse_error` | `false` | Pause the job when a message cannot be parsed at all. |

### `FROM KAFKA` (the source)

| Property | Meaning |
| --- | --- |
| `kafka_connection` | **Required in Nova.** The Kafka connection's name. Nova adds the brokers and sign-in. |
| `kafka_topic` | **Required.** The one topic the job reads. |
| `kafka_partitions` | Partitions to read, such as `"0,1,2"`. Default: all. |
| `kafka_offsets` | Start offset for each partition in `kafka_partitions`: a number, `OFFSET_BEGINNING` or `OFFSET_END`. |
| `property.kafka_default_offsets` | Start offset for every partition: `OFFSET_BEGINNING` (all messages already there) or `OFFSET_END` (only new ones, the default). |
| `property.kafka_partition_discovery` | `true` keeps adding new partitions even when `kafka_partitions` is set. |
| `property.group.id`, `property.client.id` and other `property.<name>` | Kafka consumer settings, passed to the consumer. |
| `confluent.schema.registry.url` | Avro: the schema registry address. It cannot carry a user or password. |

Nova refuses `kafka_broker_list` and every `property.security.*`,
`property.sasl.*` and `property.ssl.*` setting: they come from the connection.

## Watch a load

```sql
SHOW ROUTINE LOAD FOR kafka_demo.orders_csv;
SHOW ROUTINE LOAD FROM kafka_demo;
SHOW ALL ROUTINE LOAD FROM kafka_demo;
SHOW ROUTINE LOAD TASK FROM kafka_demo WHERE JobName = "orders_csv";
SHOW CREATE ROUTINE LOAD kafka_demo.orders_csv;
```

- `State`: `NEED_SCHEDULE` (starting), `RUNNING`, `PAUSED`, `STOPPED` or
  `CANCELLED`. `SHOW ALL` includes stopped and cancelled jobs.
- `Statistic`: `loadedRows`, `errorRows` and `committedTaskNum`.
- `Progress`: the last offset read in each partition.
  `LatestSourcePosition` is the next offset Kafka would write; the job is
  caught up when `Progress` is one less.
- `ReasonOfStateChanged`: why the job paused.
- `DataSourceProperties` shows the topic and the Kafka connection's name as
  `kafkaConnection`, not the broker addresses. `SHOW CREATE ROUTINE LOAD`
  shows `"kafka_connection" = "<connection>"` the same way. A job that was not
  created through Nova shows `hidden`.
- `CustomProperties` shows the sign-in user, never the password (`***`).
- `SHOW ROUTINE LOAD TASK` lists the running tasks and their progress.

You see the jobs of tables your role may use.

## Pause, change, resume and stop

```sql
PAUSE ROUTINE LOAD FOR kafka_demo.orders_csv;
ALTER ROUTINE LOAD FOR kafka_demo.orders_csv PROPERTIES ("max_batch_interval" = "10");
RESUME ROUTINE LOAD FOR kafka_demo.orders_csv;
STOP ROUTINE LOAD FOR kafka_demo.orders_csv;
```

- A paused job keeps its place in the topic; `RESUME` continues from there.
- `ALTER` works on a paused job. It can change job properties, `COLUMNS`,
  `WHERE`, `kafka_partitions`, `kafka_offsets` and `property.*` settings, but
  not the topic or connection: create a new job for those.
- `STOP` ends the job for good. Nova asks you to confirm it. The rows already
  loaded stay. To load again, create a new job (it may reuse the name).

## What Nova checks

| When | Check | If it fails |
| --- | --- | --- |
| `CREATE ROUTINE LOAD` | The connection exists and is Kafka; your role may load from it; your role may insert into the table; no running or paused job has the name | The statement is refused; nothing is created |
| `PAUSE`, `ALTER`, `RESUME`, `STOP` | Your role is the job's owner role, or an administrator role | Refused |
| `RESUME` | The owner still has the owner role, may load from the connection and may insert into the table | Refused with the reason |
| About every minute | The same as `RESUME`, for every running job | Nova pauses the job |

A job Nova paused shows `PAUSED` with the reason
`User root pauses routine load job` (the engine's name for Nova's control
session). `RESUME` tells you what is still missing. Once access is restored,
the owner role or an administrator resumes it; Nova never resumes a job by
itself.

Jobs made outside Nova (for example directly on the engine) can be paused and
stopped here, but not resumed or changed.

## Errors

| Message | Meaning |
| --- | --- |
| `'kafka_broker_list' comes from the Kafka connection; name the connection with "kafka_connection" = "<connection>" instead` | Use a Kafka connection instead of broker addresses or passwords. |
| `FROM KAFKA names a Nova Kafka connection: ...` | Add `"kafka_connection" = "<name>"`. |
| `Role marketing may not load from Kafka connection 'events_kafka'` | Ask a security administrator to add your role under **Roles that can load**. |
| `Role marketing may not write to kafka_demo.orders` | Your role needs `INSERT` on the table. |
| `Routine load ... belongs to role marketing; only that role or an administrator can change it` | Switch to the owner role. |
| `Routine load ... cannot resume: Role marketing may no longer write to ...` | Nova paused it because access was removed. |
| `Routine load ... is stopped; a stopped job cannot be resumed or changed` | Create a new job. |
| `Routine load ... was not created through Nova` | `RESUME` and `ALTER` work only on jobs created through Nova. |
| `Nova loads routine loads from Kafka connections only` | Pulsar is not supported. |

## Limits

- Kafka only (no Pulsar). SASL `PLAIN` and `SCRAM`; no Kerberos, client
  certificates or custom certificate files.
- After access is removed, the job may load for up to about a minute before
  Nova pauses it. Rows already loaded stay.
- One job reads one topic into one table.
