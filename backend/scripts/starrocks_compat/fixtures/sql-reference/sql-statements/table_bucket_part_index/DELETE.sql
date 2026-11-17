-- films (Primary Key) and producers for the multi-table DELETE examples.
CREATE TABLE producers (
    id INT NOT NULL,
    name VARCHAR(32)
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO producers VALUES (1, 'foo'), (2, 'bar');
CREATE TABLE films (
    id INT NOT NULL,
    title VARCHAR(64),
    producer_id INT
) PRIMARY KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO films VALUES (1, 'First Film', 1), (2, 'Second Film', 2), (3, 'Third Film', 1);
