-- t, with the rows the page prints.
CREATE TABLE t (k1 VARCHAR(8), k2 VARCHAR(8))
DUPLICATE KEY(k1) DISTRIBUTED BY RANDOM BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO t VALUES
    (NULL, 'B'), (NULL, 'b'), ('A', NULL), ('A', 'B'), ('A', 'b'), ('a', NULL), ('a', 'B'), ('a', 'b');
