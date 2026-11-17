-- example_table (unqualified, so in the page database) and example_db.example_view to alter.
CREATE DATABASE IF NOT EXISTS example_db;
CREATE TABLE example_table (k1 INT, k2 INT, v1 BIGINT) DUPLICATE KEY(k1) DISTRIBUTED BY HASH(k1) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO example_table VALUES (1, 1, 10), (1, 2, 20), (2, 1, 30);
CREATE VIEW example_db.example_view (c1, c2, c3) AS SELECT k1, k2, v1 FROM example_table;
