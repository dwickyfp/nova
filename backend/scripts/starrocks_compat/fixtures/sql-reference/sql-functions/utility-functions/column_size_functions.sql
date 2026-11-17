-- products (name, description) for the [_META_] example.
CREATE TABLE products (id INT, name VARCHAR(64), description STRING)
DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO products VALUES (1, 'apple', 'A crisp red fruit'), (2, 'banana', 'A long yellow fruit');
