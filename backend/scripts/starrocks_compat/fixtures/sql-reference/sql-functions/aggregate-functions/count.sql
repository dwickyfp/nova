-- test, with the rows the page prints.
CREATE TABLE test (id INT, country VARCHAR(32), category VARCHAR(8), supplier VARCHAR(32))
DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO test VALUES
    (1001, 'US', 'A', 'supplier_1'), (1002, 'Thailand', 'A', 'supplier_2'),
    (1003, 'Turkey', 'B', 'supplier_3'), (1004, 'US', 'A', 'supplier_2'),
    (1005, 'China', 'C', 'supplier_4'), (1006, 'Japan', 'D', 'supplier_3'),
    (1007, 'Japan', NULL, 'supplier_5');
