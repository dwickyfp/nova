-- SSB-style customer/lineorder/part and tbl for the mv1-mv3 examples, plus ssb_1g.customer for the qualified EXPLAIN.
CREATE DATABASE IF NOT EXISTS ssb_1g;
CREATE TABLE ssb_1g.customer (
    c_custkey INT NOT NULL, c_name VARCHAR(26), c_city VARCHAR(11)
) DUPLICATE KEY(c_custkey) DISTRIBUTED BY HASH(c_custkey) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO ssb_1g.customer VALUES (1, 'Customer#1', 'CHINA    1'), (2, 'Customer#2', 'BRAZIL   0');
CREATE TABLE customer (
    c_custkey INT NOT NULL, c_name VARCHAR(26), c_city VARCHAR(11)
) DUPLICATE KEY(c_custkey) DISTRIBUTED BY HASH(c_custkey) BUCKETS 1
PROPERTIES ("replication_num" = "1");
CREATE TABLE lineorder (
    lo_orderkey INT NOT NULL, lo_custkey INT, lo_partkey INT, lo_revenue INT
) DUPLICATE KEY(lo_orderkey) DISTRIBUTED BY HASH(lo_orderkey) BUCKETS 1
PROPERTIES ("replication_num" = "1");
CREATE TABLE part (
    p_partkey INT NOT NULL, p_name VARCHAR(23)
) DUPLICATE KEY(p_partkey) DISTRIBUTED BY HASH(p_partkey) BUCKETS 1
PROPERTIES ("replication_num" = "1");
CREATE TABLE tbl (
    dt DATE, c_city VARCHAR(11), tax DECIMAL(10,2)
) DUPLICATE KEY(dt) DISTRIBUTED BY RANDOM BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO customer VALUES (1, 'Customer#1', 'CHINA    1'), (2, 'Customer#2', 'BRAZIL   0');
INSERT INTO lineorder VALUES (1, 1, 1, 100), (2, 2, 2, 200);
INSERT INTO part VALUES (1, 'lace spring'), (2, 'rose moccasin');
INSERT INTO tbl VALUES ('2023-01-01', 'CHINA    1', 1.50), ('2023-01-02', 'BRAZIL   0', 2.00);
