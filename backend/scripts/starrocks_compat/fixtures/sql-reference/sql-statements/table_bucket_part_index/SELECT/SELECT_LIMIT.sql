-- small_table with the two rows the LIMIT examples print.
CREATE TABLE small_table (
    tiny_column TINYINT,
    short_column SMALLINT,
    int_column INT,
    varchar_column VARCHAR(32)
) DUPLICATE KEY(tiny_column)
DISTRIBUTED BY HASH(tiny_column) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO small_table VALUES (1, 2, 10, 'beijing'), (2, 1, 20, 'milan');
