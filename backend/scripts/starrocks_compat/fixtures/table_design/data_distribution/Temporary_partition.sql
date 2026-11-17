-- site_access and site_access_copy, range-partitioned by month (p1..p5 = Jan..May 2020), with a few rows.
CREATE TABLE site_access (
    event_day DATE,
    site_id INT DEFAULT '10',
    city_code VARCHAR(100),
    user_name VARCHAR(32) DEFAULT '',
    pv BIGINT DEFAULT '0'
)
DUPLICATE KEY(event_day, site_id, city_code, user_name)
PARTITION BY RANGE(event_day) (
    PARTITION p1 VALUES [("2020-01-01"), ("2020-02-01")),
    PARTITION p2 VALUES [("2020-02-01"), ("2020-03-01")),
    PARTITION p3 VALUES [("2020-03-01"), ("2020-04-01")),
    PARTITION p4 VALUES [("2020-04-01"), ("2020-05-01")),
    PARTITION p5 VALUES [("2020-05-01"), ("2020-06-01"))
)
DISTRIBUTED BY HASH(site_id) BUCKETS 1
PROPERTIES ("replication_num" = "1");
CREATE TABLE site_access_copy LIKE site_access;
INSERT INTO site_access VALUES
    ("2020-01-15", 1, "ca", "lily", 3),
    ("2020-02-10", 2, "ny", "tom", 5),
    ("2020-03-20", 1, "ca", "lucy", 2);
INSERT INTO site_access_copy VALUES
    ("2020-02-11", 2, "ny", "tom", 6),
    ("2020-03-21", 1, "ca", "lucy", 1),
    ("2020-04-02", 3, "tx", "jack", 7);
