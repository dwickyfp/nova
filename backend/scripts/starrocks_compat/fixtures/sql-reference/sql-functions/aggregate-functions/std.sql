-- std_test, with the rows the page prints.
CREATE TABLE std_test (col0 INT, col1 INT)
DUPLICATE KEY(col0) DISTRIBUTED BY HASH(col0) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO std_test VALUES (0, 0), (1, 2), (2, 4), (3, 6), (4, 8);
