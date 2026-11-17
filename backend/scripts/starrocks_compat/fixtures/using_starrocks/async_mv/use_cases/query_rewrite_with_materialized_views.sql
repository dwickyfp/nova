-- Tables the page queries but never creates: order_list/goods, t1/t2, TPC-H orders/lineitem and user_tags.
-- (customer, lineorder, dates, supplier and part are created by the page itself.)
CREATE TABLE order_list (
    order_id INT, order_date DATE, client_id INT, item_id2 INT
) DUPLICATE KEY(order_id) DISTRIBUTED BY HASH(order_id) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO order_list VALUES (1, '2023-07-03', 10, 1), (2, '2023-07-03', 11, 2), (3, '2023-07-04', 10, 1);
CREATE TABLE goods (
    item_id1 INT, item_name VARCHAR(32), price DECIMAL(10,2)
) DUPLICATE KEY(item_id1) DISTRIBUTED BY HASH(item_id1) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO goods VALUES (1, 'apple', 3.50), (2, 'pear', 2.00);
-- t1 serves both the generic aggregate state example (id, province, dt) and the view example (a, c).
CREATE TABLE t1 (
    id BIGINT NOT NULL,
    province VARCHAR(64),
    a INT,
    c INT,
    dt VARCHAR(10) NOT NULL
) DUPLICATE KEY(id) PARTITION BY (dt) DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO t1 VALUES (1, 'p1', 1, 10, '2024-07-24'), (2, 'p2', 2, 20, '2024-07-24'), (3, 'p1', 1, 30, '2024-07-25');
CREATE TABLE t2 (a INT, b VARCHAR(20)) DUPLICATE KEY(a) DISTRIBUTED BY HASH(a) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO t2 VALUES (1, 'one'), (2, 'two');
CREATE TABLE orders (
    o_orderkey INT NOT NULL, o_custkey INT, o_orderdate DATE, o_totalprice DECIMAL(15,2)
) DUPLICATE KEY(o_orderkey) DISTRIBUTED BY HASH(o_orderkey) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO orders VALUES (1, 1, '1996-01-02', 1000.00), (2, 2, '1996-12-01', 500.00);
CREATE TABLE lineitem (
    l_orderkey INT NOT NULL, l_partkey INT, l_suppkey INT, l_quantity DECIMAL(15,2), l_tax DECIMAL(15,2)
) DUPLICATE KEY(l_orderkey) DISTRIBUTED BY HASH(l_orderkey) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO lineitem VALUES (1, 1, 1, 200.00, 0.02), (1, 2, 1, 150.00, 0.06), (2, 1, 2, 10.00, 0.04);
CREATE TABLE user_tags (
    user_id INT, `time` DATE, tag_id INT
) DUPLICATE KEY(user_id) DISTRIBUTED BY HASH(user_id) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO user_tags VALUES (1, '2024-01-01', 100), (1, '2024-01-02', 101), (2, '2024-01-01', 100);
