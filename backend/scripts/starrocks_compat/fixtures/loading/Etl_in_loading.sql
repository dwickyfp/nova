-- table1 and table2 as the page defines them in indented steps; the loads that fill
-- them read HDFS and Kafka, so they start empty here.
CREATE TABLE table1 (event_date DATE, event_type TINYINT, user_id BIGINT)
DISTRIBUTED BY HASH(user_id) BUCKETS 1 PROPERTIES ("replication_num" = "1");
CREATE TABLE table2 (`date` DATE, `year` INT, `month` TINYINT, `day` TINYINT)
DISTRIBUTED BY HASH(`date`) BUCKETS 1 PROPERTIES ("replication_num" = "1");
