-- ipv4 (stringIp), with the rows the page prints.
CREATE TABLE ipv4 (id INT, stringIp VARCHAR(32))
DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO ipv4 VALUES (1, '0.0.0.0'), (2, '255.255.255.255'), (3, 'invalid');
