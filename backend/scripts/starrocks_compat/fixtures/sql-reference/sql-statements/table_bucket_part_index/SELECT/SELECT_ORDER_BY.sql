-- big_table and sales_record (employee_id with a NULL) for the ORDER BY examples.
CREATE TABLE big_table (
    tiny_column TINYINT,
    short_column SMALLINT,
    int_column INT,
    varchar_column VARCHAR(32)
) DUPLICATE KEY(tiny_column)
DISTRIBUTED BY HASH(tiny_column) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO big_table VALUES (1, 1, 100, 'beijing'), (1, 2, 200, 'chongqing'), (2, 3, 300, 'tianjin'), (2, 3, 300, 'tianjin');
CREATE TABLE sales_record (
    record_id INT,
    employee_id INT,
    amount INT
) DUPLICATE KEY(record_id)
DISTRIBUTED BY HASH(record_id) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO sales_record VALUES (1, 102, 300), (2, NULL, 150), (3, 101, 200);
