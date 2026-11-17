-- db.table_with_json with a JSON column for the CAST(json_col AS VARIANT) example (Iceberg examples need a catalog).
CREATE DATABASE IF NOT EXISTS db;
CREATE TABLE db.table_with_json (id INT, json_col JSON) DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO db.table_with_json VALUES (1, parse_json('{"id": 1, "flags": {"active": true}, "scores": [1.5, null]}'));
