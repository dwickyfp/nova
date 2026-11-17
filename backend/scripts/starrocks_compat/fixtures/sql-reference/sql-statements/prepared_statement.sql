-- users(id, country, city, revenue) in the page database for the PREPARE example (the page's later demo.users is separate).
CREATE TABLE users (id BIGINT NOT NULL, country STRING, city STRING, revenue BIGINT)
PRIMARY KEY (id) DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO users VALUES (1, 'US', 'New York', 100), (2, 'UK', 'London', 200);
