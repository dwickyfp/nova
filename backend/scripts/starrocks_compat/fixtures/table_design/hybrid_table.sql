-- users with the rows the page prints. The page's CREATE needs FE config enable_experimental_rowstore
-- ("store_type" = "column_with_row" is rejected without it), so this is the same Primary Key table without that property.
CREATE TABLE users (
  id bigint not null,
  country string,
  city string,
  revenue bigint
)
PRIMARY KEY (id)
DISTRIBUTED BY HASH (id) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO users (id, country, city, revenue) VALUES
  (1, 'USA', 'New York', 5000),
  (2, 'UK', 'London', 4500),
  (3, 'France', 'Paris', 6000),
  (4, 'Germany', 'Berlin', 6500),
  (5, 'Japan', 'Tokyo', 7000);
