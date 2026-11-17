-- table1..table5 as the page defines them in indented steps (the probe does not extract
-- those); the page fills them with Stream Load (curl), so they start empty here.
CREATE TABLE table1 (id INT NOT NULL, name VARCHAR(65533) NOT NULL, score INT NOT NULL)
PRIMARY KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES ("replication_num" = "1");
CREATE TABLE table2 (id INT NOT NULL, name VARCHAR(65533) NOT NULL, score INT NOT NULL)
PRIMARY KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES ("replication_num" = "1");
CREATE TABLE table3 (id INT NOT NULL, name VARCHAR(65533) NOT NULL, score INT NOT NULL)
PRIMARY KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES ("replication_num" = "1");
CREATE TABLE table4 (id INT NOT NULL, name VARCHAR(65533) NOT NULL, score INT NOT NULL)
PRIMARY KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES ("replication_num" = "1");
CREATE TABLE table5 (id INT NOT NULL, version INT NOT NULL, score INT NOT NULL)
PRIMARY KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO table5 VALUES (1, 10, 100);
