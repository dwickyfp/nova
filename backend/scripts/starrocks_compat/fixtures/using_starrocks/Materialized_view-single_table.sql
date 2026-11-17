-- tableA with sort keys k1, k2 and a value column k3, for the extra-sort-key materialized view.
CREATE TABLE tableA (
    k1 INT, k2 INT, k3 INT
) DUPLICATE KEY(k1, k2) DISTRIBUTED BY HASH(k1) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO tableA VALUES (1, 1, 10), (2, 1, 20), (3, 2, 10);
