-- agg, with the rows the page prints.
CREATE TABLE agg (no INT, k DECIMAL(10,2), v DECIMAL(10,2))
DUPLICATE KEY(no) DISTRIBUTED BY HASH(no) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO agg VALUES (1, 10.00, NULL), (2, 10.00, 11.00), (2, 20.00, 22.00), (2, 25.00, NULL), (2, 30.00, 35.00);
