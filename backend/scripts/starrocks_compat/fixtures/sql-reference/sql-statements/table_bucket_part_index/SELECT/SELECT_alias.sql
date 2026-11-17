-- big_table and small_table for the alias examples.
CREATE TABLE big_table (
    tiny_column TINYINT,
    short_column SMALLINT,
    int_column INT,
    varchar_column VARCHAR(32)
) DUPLICATE KEY(tiny_column)
DISTRIBUTED BY HASH(tiny_column) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO big_table VALUES (1, 1, 100, 'beijing'), (1, 2, 200, 'chongqing'), (2, 3, 300, 'tianjin'), (2, 3, 300, 'tianjin');
CREATE TABLE small_table (
    tiny_column TINYINT,
    short_column SMALLINT,
    int_column INT,
    varchar_column VARCHAR(32)
) DUPLICATE KEY(tiny_column)
DISTRIBUTED BY HASH(tiny_column) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO small_table VALUES (1, 2, 10, 'beijing'), (2, 1, 20, 'milan');
