-- olap_db.olap_table for the three ways of naming a default-catalog table.
CREATE DATABASE IF NOT EXISTS olap_db;
CREATE TABLE olap_db.olap_table (id INT, name VARCHAR(20))
DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO olap_db.olap_table VALUES (1, 'a');
