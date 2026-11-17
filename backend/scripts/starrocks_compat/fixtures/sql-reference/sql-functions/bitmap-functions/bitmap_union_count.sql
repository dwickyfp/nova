-- test (page_id, INT user_id), with the rows the page prints.
CREATE TABLE test (page_id INT, user_id INT)
DUPLICATE KEY(page_id) DISTRIBUTED BY HASH(page_id) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO test VALUES (1, 1), (1, 2), (2, 1);
