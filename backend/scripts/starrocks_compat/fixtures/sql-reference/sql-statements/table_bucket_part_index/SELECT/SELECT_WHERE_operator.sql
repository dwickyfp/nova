-- t1 (c1, month) and small_table (one varchar_column starting with m: milan) for the WHERE examples.
CREATE TABLE t1 (
    c1 INT,
    month INT
) DUPLICATE KEY(c1)
DISTRIBUTED BY HASH(c1) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO t1 VALUES (1, 3), (2, 7), (3, 6);
CREATE TABLE small_table (
    tiny_column TINYINT,
    short_column SMALLINT,
    int_column INT,
    varchar_column VARCHAR(32)
) DUPLICATE KEY(tiny_column)
DISTRIBUTED BY HASH(tiny_column) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO small_table VALUES (1, 2, 10, 'beijing'), (2, 1, 20, 'milan');
