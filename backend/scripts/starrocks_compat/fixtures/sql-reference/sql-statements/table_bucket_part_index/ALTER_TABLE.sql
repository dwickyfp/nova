-- t1 (range-partitioned on dt) and table1/table2 (partitions p1-p3, column siteid) for the ALTER TABLE examples.
CREATE TABLE t1 (
    dt DATE NOT NULL,
    id INT,
    v INT
) DUPLICATE KEY(dt)
PARTITION BY RANGE(dt) (
    PARTITION p202401 VALUES LESS THAN ("2024-02-01"),
    PARTITION p202402 VALUES LESS THAN ("2024-03-01"),
    PARTITION pmax VALUES LESS THAN (MAXVALUE)
)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO t1 VALUES ('2024-01-15', 1, 10), ('2024-02-15', 2, 20), (CURRENT_DATE(), 3, 30);
CREATE TABLE table1 (
    event_day DATE NOT NULL,
    siteid INT,
    citycode SMALLINT,
    username VARCHAR(32),
    pv BIGINT
) DUPLICATE KEY(event_day, siteid)
PARTITION BY RANGE(event_day) (
    PARTITION p1 VALUES LESS THAN ("2024-01-01"),
    PARTITION p2 VALUES LESS THAN ("2024-02-01"),
    PARTITION p3 VALUES LESS THAN ("2024-03-01")
)
DISTRIBUTED BY HASH(siteid) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO table1 VALUES ('2023-12-20', 1, 10, 'alice', 3), ('2024-01-10', 2, 20, 'bob', 5), ('2024-02-05', 3, 30, 'carol', 7);
CREATE TABLE table2 (
    event_day DATE NOT NULL,
    siteid INT,
    citycode SMALLINT,
    username VARCHAR(32),
    pv BIGINT
) DUPLICATE KEY(event_day, siteid)
DISTRIBUTED BY HASH(siteid) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO table2 VALUES ('2024-01-10', 4, 40, 'dave', 1);
