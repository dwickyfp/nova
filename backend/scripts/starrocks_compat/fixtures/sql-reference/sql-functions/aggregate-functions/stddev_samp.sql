-- log_statis, a small query log the examples aggregate.
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
