-- test_case: the page's CREATE has a trailing comma (docs syntax error); the page inserts the rows.
CREATE TABLE test_case (name STRING, gender INT)
DUPLICATE KEY(name) DISTRIBUTED BY HASH(name) BUCKETS 1 PROPERTIES ("replication_num" = "1");
