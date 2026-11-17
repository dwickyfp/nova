-- sales: the page's CREATE puts PARTITION BY after DISTRIBUTED BY (docs syntax error); same table with the clauses in order.
CREATE TABLE sales (
  sale_id bigint,
  customer_id int,
  sale_date date,
  amount decimal(10,2)
) ENGINE = OLAP
PARTITION BY date_trunc('DAY', sale_date)
DISTRIBUTED BY HASH(customer_id) BUCKETS 48
PROPERTIES ("colocate_with" = "group1", "replication_num" = "1");
INSERT INTO sales VALUES
  (1, 123, '2025-01-05', 120.50),
  (2, 456, '2025-01-05', 80.00),
  (3, 123, '2025-01-20', 45.25);
