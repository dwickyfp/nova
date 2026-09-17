#!/usr/bin/env bash
# Seed the L3 integration environment: users, system schema, audit table,
# stage row, and the stage object in MinIO.
#
# The MySQL-proxy acceptance suite assumes this environment already exists —
# it documents "start the engine first" and provisions none of it. This script
# is that prerequisite, in one place, so CI and a developer run the same steps.
#
# Idempotent: safe to run repeatedly against a live stack.
#
# Usage, from backend/ with `docker compose -f docker-compose.test.yml` up:
#   bash tests/integration/seed_engine.sh
#
# Overridable: STARROCKS_PORT (host, default 29030), S3_PORT (default 29000),
# INIT_SQL (default ../docker/init-nova.sql relative to this file's repo root).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
COMPOSE_FILE="docker-compose.test.yml"
PROJECT_NAME="$(basename "$BACKEND_DIR")"

SR_PORT="${STARROCKS_PORT:-29030}"
S3_PORT="${S3_PORT:-29000}"
INIT_SQL="${INIT_SQL:-$REPO_ROOT/docker/init-nova.sql}"
MC_IMAGE="quay.io/minio/mc:RELEASE.2025-04-16T18-13-26Z"

compose() { docker compose -f "$COMPOSE_FILE" "$@"; }
cd "$BACKEND_DIR"

fe_sql() {
  compose exec -T starrocks-fe mysql -h 127.0.0.1 -P 9030 -u root "$@"
}

echo "== system schema =="
# The repo's init script is authoritative for NOVA_SYSTEM. It currently aborts
# partway through (see the ML_MODEL_ALIASES note below), so failures are
# tolerated here and the tables afterwards are created explicitly.
if [ -f "$INIT_SQL" ]; then
  fe_sql < "$INIT_SQL" || true
else
  echo "  init script not found at $INIT_SQL; relying on explicit DDL below"
fi

# AUDIT_LOG is missing whenever init-nova.sql aborts before it, and the proxy
# writes an audit row for every statement, so nothing works without it.
#
# Key columns are a strict prefix of the schema in declaration order:
# `DUPLICATE KEY(log_id, event_type, event_time)` skips query_id and StarRocks
# rejects it with "Key columns must be the first few columns of the schema".
fe_sql -e "
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.AUDIT_LOG (
  log_id        BIGINT NOT NULL AUTO_INCREMENT,
  query_id      VARCHAR(36),
  event_type    VARCHAR(64) NOT NULL,
  event_time    DATETIME NOT NULL,
  user_name     VARCHAR(128),
  ip_address    VARCHAR(45),
  object_type   VARCHAR(64),
  object_name   VARCHAR(512),
  action        VARCHAR(128),
  sql_text      TEXT,
  status        VARCHAR(32),
  error_message TEXT,
  duration_ms   BIGINT,
  rows_affected BIGINT,
  client_ip     VARCHAR(45),
  session_id    VARCHAR(64),
  rewritten_sql TEXT,
  file_id       VARCHAR(64),
  database_name VARCHAR(128),
  schema_name   VARCHAR(128)
) DUPLICATE KEY(log_id, query_id, event_type, event_time)
DISTRIBUTED BY HASH(log_id) BUCKETS 8
PROPERTIES('replication_num'='1');
"

echo "== users and databases =="
# init-nova.sql creates nova_admin with its own password ('!1password') and
# `CREATE USER IF NOT EXISTS` will not change it, so the acceptance suite's
# expected credential is set explicitly here. The suite defaults to
# NovaProxy2026! (test_mysql_proxy_cli.py:42); keep the two in step.
fe_sql -e "
CREATE USER IF NOT EXISTS 'nova_admin' IDENTIFIED BY 'NovaProxy2026!';
ALTER USER 'nova_admin' IDENTIFIED BY 'NovaProxy2026!';
GRANT ALL ON *.* TO 'nova_admin' WITH GRANT OPTION;
CREATE DATABASE IF NOT EXISTS NOVA_ANALYTICS;
CREATE DATABASE IF NOT EXISTS NOVA_SYSTEM;
-- NOVA_DEMO exists so the proxy's \`USE <db>\` session tracking has a real
-- database to switch into; the test asserts DATABASE() reports it back.
-- (init-nova.sql also creates it when it runs to completion.)
CREATE DATABASE IF NOT EXISTS NOVA_DEMO;
"

echo "== stage row =="
# `storage_connection` names a connection in docker/nova.yaml (bucket 'stages'),
# and base_prefix is what the translator joins the stage path onto.
fe_sql -e "
INSERT INTO NOVA_SYSTEM.CONFIG_STAGES
  (id, name, database_name, schema_name, storage_connection, base_prefix, created_by)
VALUES ('stage-products', 'products', 'NOVA_ANALYTICS', 'public', 'production',
        'NOVA_ANALYTICS/public/products', 'root');
"

echo "== stage object in MinIO =="
CSV="$(mktemp -t nova-products-XXXXXX.csv)"
printf 'id,name,amount\n1,widget,10\n2,gadget,20\n3,doohickey,30\n' > "$CSV"
NETWORK="${PROJECT_NAME}_default"
mc() {
  docker run --rm --network "$NETWORK" -v "$CSV":/products_new.csv \
    -e MC_HOST_local="http://minioadmin:minioadmin@minio:${S3_PORT}" \
    "$MC_IMAGE" "$@"
}
mc mb --ignore-existing local/stages
mc cp /products_new.csv local/stages/NOVA_ANALYTICS/public/products/products_new.csv
rm -f "$CSV"

echo "== verify =="
fe_sql -e "SHOW BACKENDS" | grep -q . && echo "  backends: present"
fe_sql -e "SELECT 1" >/dev/null && echo "  query path: ok"
echo "seed complete"
