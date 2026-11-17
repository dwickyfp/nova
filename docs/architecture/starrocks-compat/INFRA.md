# StarRocks compat test infrastructure: Kafka and Iceberg REST

Decision (user, 2026-11-16): Kafka and an Iceberg REST catalog run as new
services in the existing `docker` Compose project, with table data in the
existing MinIO. Existing services and their data are not changed.

These are development test fixtures for Routine Load and Iceberg catalogs
(see [PLAN.md](PLAN.md)). They are not part of the governed Nova runtime or
the production Compose target. Nova still refuses native Routine Load and
`CREATE EXTERNAL CATALOG` in the product path; everything below was run
directly on the engine.

## Services

All three are in `docker/docker-compose-engine.yml` under the profile
`starrocks-compat`, on the `nova` network next to `starrocks-fe` and
`starrocks-be`.

| Service | Image (pinned by digest in the file) | Inside the network | Host port | Limits |
| --- | --- | --- | --- | --- |
| `kafka` (`nova-kafka`) | `apache/kafka:3.9.1` | `kafka:9092` | `127.0.0.1:29092` (`KAFKA_HOST_PORT`) | 1 GiB memory, heap 256-512 MB, 1 CPU, 512 PIDs |
| `iceberg-rest` (`nova-iceberg-rest`) | `apache/iceberg-rest-fixture:1.10.0` | `http://iceberg-rest:8181` | `127.0.0.1:28181` (`ICEBERG_REST_HOST_PORT`) | 512 MiB memory, heap up to 384 MB, 0.5 CPU, 256 PIDs |
| `iceberg-minio-init` (`nova-iceberg-minio-init`) | `ghcr.io/teableio/minio-mc` (same tag as `minio-init`) | one-shot | none | exits after setup |

- **Kafka:** one KRaft node (broker and controller, no ZooKeeper). Listener
  `INTERNAL` (`kafka:9092`) is for containers; `HOST` is advertised as
  `localhost:29092` for producers on this machine. Retention is 72 hours or
  512 MB per partition, 64 MB segments. Data volume `docker_kafka_data`.
  Topics are auto-created on first write (Kafka default).
- **Iceberg REST:** JDBC catalog in SQLite at
  `/home/iceberg/iceberg_catalog.db` on volume `docker_iceberg_catalog_data`,
  warehouse `s3://nova-iceberg/warehouse`, `S3FileIO` against
  `http://minio:9000` with path-style access and region `us-east-1`. No
  authentication on the REST endpoint (dev fixture; loopback-only host port).
- **MinIO:** `iceberg-minio-init` creates bucket `nova-iceberg` and a MinIO
  user (`ICEBERG_S3_ACCESS_KEY`) with policy `nova-iceberg-rw`, which allows
  read, write, list and delete on that bucket only. Other buckets and users
  are not touched. It is idempotent.
- All services use the stack's log rotation (json-file, 3 x 10 MB) and have
  healthchecks (`kafka-broker-api-versions.sh` every 30 s; `GET /v1/config`
  every 15 s).

Credentials: `ICEBERG_S3_ACCESS_KEY` and `ICEBERG_S3_SECRET_KEY` live in the
git-ignored `docker/.env`; `docker/.env.example` has the placeholders and says
how to generate the secret.

## Start and stop

The running project uses three Compose files and the `docker` directory as
project directory. Run from the repository root:

```bash
dc() {
  docker compose -p docker --project-directory docker --env-file docker/.env \
    -f "$HOME/.local/share/nova/migration-20261113/pg-provisioning/compose.yaml" \
    -f docker/docker-compose-engine.yml -f docker/docker-compose.dev.yml \
    --profile starrocks-compat "$@"
}

dc up -d --no-deps iceberg-minio-init kafka   # bucket/user, then Kafka
dc up -d --no-deps iceberg-rest               # after the init container exited 0
dc ps kafka iceberg-rest
dc stop kafka iceberg-rest                    # stop only the new services
```

Always name the services and pass `--no-deps`, so Compose does not touch
StarRocks, MinIO or the other running services. Without `--profile
starrocks-compat`, a plain `up -d` of the stack does not start them. To remove
them completely: `dc rm -sf kafka iceberg-rest iceberg-minio-init`, then
`docker volume rm docker_kafka_data docker_iceberg_catalog_data` (this deletes
topics and catalog metadata; the files in `nova-iceberg` stay in MinIO).

Produce test messages:

```bash
printf '1,alice,10.50\n2,bob,20.00\n' | docker exec -i -e KAFKA_HEAP_OPTS=-Xmx128m \
  nova-kafka /opt/kafka/bin/kafka-console-producer.sh \
  --bootstrap-server kafka:9092 --topic nova_compat_orders
```

## Verified engine properties (StarRocks 4.1.4, patched FE)

### Iceberg REST catalog

Minimal set that works (create, insert and select verified):

```sql
CREATE EXTERNAL CATALOG compat_iceberg PROPERTIES (
  "type" = "iceberg",
  "iceberg.catalog.type" = "rest",
  "iceberg.catalog.uri" = "http://iceberg-rest:8181",
  "aws.s3.endpoint" = "http://minio:9000",
  "aws.s3.enable_path_style_access" = "true",
  "aws.s3.region" = "us-east-1",
  "aws.s3.access_key" = "<ICEBERG_S3_ACCESS_KEY>",
  "aws.s3.secret_key" = "<ICEBERG_S3_SECRET_KEY>"
);
```

Also accepted and verified: `"iceberg.catalog.warehouse" =
"s3://nova-iceberg/warehouse"`, `"aws.s3.use_instance_profile" = "false"`,
`"client.factory" = "com.starrocks.connector.share.iceberg.IcebergAwsClientFactory"`.
None is required with this REST server, which supplies the warehouse.

- Use the service names (`iceberg-rest`, `minio`): FE and BE resolve them on
  the `nova` network. `localhost` or the host ports do not work from inside
  the engine.
- Path-style access is required for MinIO.
- `SHOW CREATE CATALOG` masks the keys to their first and last two characters.
- Metadata is cached per catalog: a row inserted through a second catalog on
  the same REST server was not visible in the first until
  `REFRESH EXTERNAL TABLE <catalog>.<db>.<table>`.
- `DROP TABLE ... FORCE` removed the table from the REST catalog but left its
  data and metadata files in MinIO; remove the prefix with `mc rm -r` when
  cleaning up.

### Routine Load

```sql
-- CSV
CREATE ROUTINE LOAD <db>.orders_csv_job ON orders_csv
COLUMNS TERMINATED BY ",", COLUMNS (order_id, customer, amount)
PROPERTIES ("desired_concurrent_number" = "1", "format" = "csv", "max_batch_interval" = "5")
FROM KAFKA ("kafka_broker_list" = "kafka:9092", "kafka_topic" = "nova_compat_orders",
            "kafka_partitions" = "0", "property.kafka_default_offsets" = "OFFSET_BEGINNING");

-- JSON
CREATE ROUTINE LOAD <db>.orders_json_job ON orders_json
COLUMNS (order_id, customer, amount)
PROPERTIES ("desired_concurrent_number" = "1", "format" = "json",
            "jsonpaths" = "[\"$.order_id\",\"$.customer\",\"$.amount\"]", "max_batch_interval" = "5")
FROM KAFKA ("kafka_broker_list" = "kafka:9092", "kafka_topic" = "nova_compat_orders_json",
            "property.kafka_default_offsets" = "OFFSET_BEGINNING");
```

- Broker list is `kafka:9092` (the `INTERNAL` listener). `localhost:29092` is
  for host tools only and is wrong inside the engine.
- No FE or BE configuration change was needed for either feature.

## Verification, 2026-11-16

Run on the shared development stack (`docker` project) as `nova_admin`
through `127.0.0.1:29030`, with the password read from `docker/.env` inside
the script. Scratch objects only, all removed.

1. `docker compose ls` / `docker ps`: one `docker` project, existing
   containers kept their uptimes before and after (FE 2 hours, BE 5 days,
   MinIO and Redis 8 days).
2. `docker compose ... config --quiet` passed; `up --dry-run --no-deps kafka
   iceberg-minio-init` showed only the new containers and volume being created.
3. `iceberg-minio-init` exited 0; `mc ls` showed `nova-iceberg` next to the
   untouched `exports`, `stages`, `temp` and `test-stage`.
4. Both services became `healthy`. `GET 127.0.0.1:28181/v1/config` returned
   the endpoint list; port 29092 accepted connections on loopback.
5. Topics `nova_compat_orders` (3 CSV rows) and `nova_compat_orders_json`
   (3 JSON rows), end offsets `0:3` each.
6. Iceberg: `CREATE EXTERNAL CATALOG compat_iceberg`, `CREATE DATABASE
   compat_iceberg.compat_ice_db`, a table partitioned by `dt`, `INSERT` of 3
   rows (OK 3), `SELECT` returned the 3 rows; `GROUP BY dt` gave
   2026-11-16: 2 rows, 30.5 and 2026-11-17: 1 row, 7.25. Parquet data and
   metadata JSON appeared under `nova-iceberg/warehouse/compat_ice_db/orders/`.
   A second catalog with the minimal properties read the table (3 rows) and
   inserted one more; after `REFRESH EXTERNAL TABLE` the first catalog saw 4.
   Then `DROP TABLE ... FORCE`, `DROP DATABASE`, `DROP CATALOG` (both), the
   REST namespace list was empty, and the leftover scratch files were removed.
7. Routine Load in `compat_rl_20261116`: both jobs `RUNNING`; all 6 rows
   arrived within about 10 s (`loadedRows` 3 each, `errorRows` 0,
   `committedTaskNum` 1). One more CSV message produced while running arrived
   within about 10 s (4 rows). `STOP ROUTINE LOAD` moved both to `STOPPED`;
   `DROP DATABASE compat_rl_20261116 FORCE`; no `compat_rl%` database remains.
   `SHOW ROUTINE LOAD` reported `OffsetLag` `{"0":"1"}` with every message
   loaded (`Progress` is the last consumed offset, `LatestSourcePosition` the
   next one), so read lag from `Progress` against the topic end offset.
8. Idle usage after the tests: Kafka about 190 MiB of 1 GiB, Iceberg REST
   about 90 MiB of 512 MiB.

## Open

- Since W8/W9 (2026-11-16) Nova supports Kafka Routine Load and Iceberg REST
  catalogs through Nova connections ([ROUTINE_LOAD.md](ROUTINE_LOAD.md),
  `docs/reports/starrocks-compat/w8-routine-load-live.json`,
  `w9-iceberg-live.json`), including Ranger checks as a non-admin role.
  The backend on the host reaches the REST service for object kinds through
  `CATALOG_DIRECT_HOSTS` (`iceberg-rest` → `nova-iceberg-rest.orb.local`).
- No TLS or SASL on Kafka and no auth on the REST catalog: acceptable only as
  loopback dev fixtures.
- Kafka 3.9.1 was chosen over 4.x to keep older client protocol versions
  available to the engine's Kafka client; 4.x was not tested.
