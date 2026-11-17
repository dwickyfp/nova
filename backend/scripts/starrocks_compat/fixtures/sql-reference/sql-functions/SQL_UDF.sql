-- users, products, events, my_table and t: the tables the SQL UDF examples query.
CREATE TABLE users (id INT, username VARCHAR(64))
DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO users VALUES (1, 'alice'), (2, 'bob');
CREATE TABLE products (id INT, price DECIMAL(10,2))
DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO products VALUES (1, 100.00), (2, 59.90);
CREATE TABLE events (id INT, user_data STRING, create_time DATETIME)
DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO events VALUES
    (1, '{"email": {"value": "alice@example.com"}}', '2024-01-15 10:00:00'),
    (2, '{"email": {"value": "bob@example.com"}}', '2024-02-03 08:30:00');
CREATE TABLE my_table (id INT, create_time DATETIME)
DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO my_table VALUES (1, '2024-03-07 12:00:00');
CREATE TABLE t (a INT)
DUPLICATE KEY(a) DISTRIBUTED BY HASH(a) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO t VALUES (3), (5), (8);
