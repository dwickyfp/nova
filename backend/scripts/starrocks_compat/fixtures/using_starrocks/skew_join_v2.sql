-- orders/customers with the page's skewed sample rows (its setup blocks sit in list items), plus table1, t2, events
-- and sales for the data-type and join-type examples (there t2 is the right-hand table name).
CREATE TABLE orders (
    order_id INT,
    customer_id INT,
    order_date DATE,
    amount DECIMAL(10,2)
) DUPLICATE KEY(order_id)
DISTRIBUTED BY HASH(order_id) BUCKETS 1
PROPERTIES ("replication_num" = "1");
CREATE TABLE customers (
    customer_id INT,
    customer_name VARCHAR(100),
    city VARCHAR(50)
) DUPLICATE KEY(customer_id)
DISTRIBUTED BY HASH(customer_id) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO orders VALUES
    (1, 1001, '2024-01-01', 100.00),
    (2, 1001, '2024-01-02', 200.00),
    (3, 1001, '2024-01-03', 150.00),
    (4, 1002, '2024-01-01', 300.00),
    (5, 1003, '2024-01-01', 250.00);
INSERT INTO customers VALUES
    (1001, 'John Doe', 'New York'),
    (1002, 'Jane Smith', 'Los Angeles'),
    (1003, 'Bob Johnson', 'Chicago');
CREATE TABLE table1 (id INT, category VARCHAR(32), region VARCHAR(32)) DUPLICATE KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES ("replication_num" = "1");
CREATE TABLE t2 (
    id INT, category VARCHAR(32), region VARCHAR(32), name VARCHAR(64),
    event_date DATE, event_name VARCHAR(64), region_id INT
) DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES ("replication_num" = "1");
CREATE TABLE events (id INT, event_date DATE) DUPLICATE KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES ("replication_num" = "1");
CREATE TABLE sales (id INT, region_id INT) DUPLICATE KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO table1 VALUES (1, 'electronics', 'east'), (2, 'books', 'west'), (3, 'electronics', 'west');
INSERT INTO t2 VALUES
    (1, 'electronics', 'east', 'gadgets', '2024-01-01', 'launch', 1),
    (2, 'books', 'west', 'novels', '2024-01-02', 'sale', 2);
INSERT INTO events VALUES (1, '2024-01-01'), (2, '2024-01-01'), (3, '2024-01-02');
INSERT INTO sales VALUES (1, 1), (2, 1), (3, 3);
