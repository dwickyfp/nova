-- events (row_number example) and example_table (QUALIFY example); the page creates its other tables.
CREATE TABLE events (date_and_time DATETIME, c1 INT, c2 INT, c3 INT, c4 INT)
DUPLICATE KEY(date_and_time) DISTRIBUTED BY HASH(c1) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO events VALUES
    ('2024-01-01 09:00:00', 1, 10, 100, 1000),
    ('2024-01-01 08:00:00', 2, 20, 200, 2000),
    ('2024-01-02 07:30:00', 3, 30, 300, 3000);
CREATE TABLE example_table (`DATE` DATE, PROVINCE_CODE VARCHAR(10), TOTAL_SCORE INT)
DUPLICATE KEY(`DATE`) DISTRIBUTED BY HASH(PROVINCE_CODE) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO example_table VALUES
    ('2024-01-01', 'BJ', 85), ('2024-01-01', 'BJ', 92),
    ('2024-01-01', 'SH', 78), ('2024-01-02', 'SH', 88);
