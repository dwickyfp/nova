-- log_statis (a small query log) and t1 (c2 = 0..50000) for the examples.
CREATE TABLE log_statis (
    `datetime` DATE,
    `table` VARCHAR(32),
    query_id VARCHAR(64),
    cost_time INT,
    scan_rows BIGINT
) DUPLICATE KEY(`datetime`) DISTRIBUTED BY HASH(query_id) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO log_statis VALUES
    ('2024-01-01', 'test', 'q1', 12, 100),
    ('2024-01-01', 'test', 'q2', 30, 2500),
    ('2024-01-01', 'test', 'q3', 30, 800),
    ('2024-01-02', 'test', 'q4', 55, 40),
    ('2024-01-02', 'test', 'q5', 8, 1200);
CREATE TABLE t1 (c1 INT, c2 INT)
DUPLICATE KEY(c1) DISTRIBUTED BY HASH(c1) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO t1 VALUES (0, 0), (1, 5000), (2, 10000), (3, 15000), (4, 20000), (5, 25000), (6, 30000), (7, 35000), (8, 40000), (9, 45000), (10, 50000);
