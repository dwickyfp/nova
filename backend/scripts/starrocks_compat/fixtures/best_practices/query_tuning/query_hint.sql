-- sales_orders and users for the SET_VAR / SET_USER_VARIABLE hints, colocated t1/t2 for the join hints.
CREATE TABLE sales_orders (
    order_id BIGINT,
    region VARCHAR(32),
    sales_amount DECIMAL(10,2),
    name VARCHAR(64),
    age INT
) DUPLICATE KEY(order_id) DISTRIBUTED BY HASH(order_id) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO sales_orders VALUES
    (1, 'east', 100.00, 'Alice', 30),
    (2, 'west', 250.50, 'Bob', 45),
    (3, 'east', 75.25, 'Alice', 45);
CREATE TABLE users (
    id BIGINT,
    name VARCHAR(64),
    age INT
) DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO users VALUES (1, 'Alice', 45), (2, 'Bob', 30);
CREATE TABLE t1 (k1 INT, v1 INT) DUPLICATE KEY(k1) DISTRIBUTED BY HASH(k1) BUCKETS 1
PROPERTIES ("replication_num" = "1", "colocate_with" = "hint_group");
CREATE TABLE t2 (k2 INT, v2 INT) DUPLICATE KEY(k2) DISTRIBUTED BY HASH(k2) BUCKETS 1
PROPERTIES ("replication_num" = "1", "colocate_with" = "hint_group");
INSERT INTO t1 VALUES (1, 10), (2, 20);
INSERT INTO t2 VALUES (1, 100), (3, 300);
