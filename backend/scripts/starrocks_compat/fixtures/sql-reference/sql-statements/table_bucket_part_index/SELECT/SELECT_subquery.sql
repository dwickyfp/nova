-- t1 (x, y) and t2 (x, y, a, b) for the subquery examples.
CREATE TABLE t1 (
    x INT,
    y INT
) DUPLICATE KEY(x)
DISTRIBUTED BY HASH(x) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO t1 VALUES (1, 1), (2, 2), (3, 3);
CREATE TABLE t2 (
    x INT,
    y INT,
    a INT,
    b INT
) DUPLICATE KEY(x)
DISTRIBUTED BY HASH(x) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO t2 VALUES (1, 1, 1, 1), (2, 3, 2, 2);
