-- tree_data (self-join), t1/t2 (id joins; c1, c2 live in t1) and holdings/prices (ASOF join) for the JOIN examples.
CREATE TABLE tree_data (
    id INT,
    parent INT,
    c1 VARCHAR(16),
    c2 VARCHAR(16)
) DUPLICATE KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO tree_data VALUES (1, NULL, 'root', 'r'), (2, 1, 'child-a', 'a'), (3, 1, 'child-b', 'b');
CREATE TABLE t1 (
    id INT,
    c1 VARCHAR(16),
    c2 VARCHAR(16)
) DUPLICATE KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO t1 VALUES (1, 'a', 'x'), (2, 'b', 'y'), (3, 'c', 'z');
CREATE TABLE t2 (
    id INT,
    v INT
) DUPLICATE KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO t2 VALUES (2, 20), (3, 30), (4, 40);
CREATE TABLE holdings (
    ticker VARCHAR(8),
    `when` DATETIME,
    shares DOUBLE
) DUPLICATE KEY(ticker)
DISTRIBUTED BY HASH(ticker) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO holdings VALUES ('APPL', '2001-01-01 00:00:30', 5.16), ('APPL', '2001-01-01 00:01:30', 2.94), ('MSFT', '2001-01-01 00:01:00', 24.13);
CREATE TABLE prices (
    ticker VARCHAR(8),
    `when` DATETIME,
    price DOUBLE
) DUPLICATE KEY(ticker)
DISTRIBUTED BY HASH(ticker) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO prices VALUES ('APPL', '2001-01-01 00:00:00', 1), ('APPL', '2001-01-01 00:01:00', 2), ('MSFT', '2001-01-01 00:00:00', 3), ('MSFT', '2001-01-01 00:01:00', 4);
