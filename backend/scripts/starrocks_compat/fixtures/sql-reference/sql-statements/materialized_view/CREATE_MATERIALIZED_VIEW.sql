-- Base tables the examples assume: t1 (as in table_design/table_types/aggregate_table.md), duplicate_table,
-- all_type_table and the SSB lineorder table the page defines in a block the probe does not run.
CREATE TABLE t1 (
  id BIGINT NOT NULL,
  province VARCHAR(64),
  age SMALLINT,
  dt VARCHAR(10) NOT NULL
)
DUPLICATE KEY(id)
PARTITION BY (dt)
DISTRIBUTED BY HASH(id) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO t1 VALUES (1, 'bj', 20, '2024-07-24'), (2, 'sh', 30, '2024-07-24'), (3, 'bj', 40, '2024-07-25');
CREATE TABLE duplicate_table (k1 INT, k2 INT, k3 BIGINT, k4 BIGINT)
DUPLICATE KEY(k1, k2, k3, k4) DISTRIBUTED BY HASH(k1) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO duplicate_table VALUES (1, 1, 10, 100), (1, 2, 20, 200), (2, 1, 30, 300);
CREATE TABLE all_type_table (
  k1 TINYINT, k2 SMALLINT, k3 INT, k4 BIGINT, k5 DECIMAL(9,0), k6 DOUBLE, k7 VARCHAR(20)
) DUPLICATE KEY(k1, k2, k3, k4, k5) DISTRIBUTED BY HASH(k1) BUCKETS 1 PROPERTIES ("replication_num" = "1");
CREATE TABLE `lineorder` (
  `lo_orderkey` int(11) NOT NULL COMMENT "",
  `lo_linenumber` int(11) NOT NULL COMMENT "",
  `lo_custkey` int(11) NOT NULL COMMENT "",
  `lo_partkey` int(11) NOT NULL COMMENT "",
  `lo_suppkey` int(11) NOT NULL COMMENT "",
  `lo_orderdate` int(11) NOT NULL COMMENT "",
  `lo_orderpriority` varchar(16) NOT NULL COMMENT "",
  `lo_shippriority` int(11) NOT NULL COMMENT "",
  `lo_quantity` int(11) NOT NULL COMMENT "",
  `lo_extendedprice` int(11) NOT NULL COMMENT "",
  `lo_ordtotalprice` int(11) NOT NULL COMMENT "",
  `lo_discount` int(11) NOT NULL COMMENT "",
  `lo_revenue` int(11) NOT NULL COMMENT "",
  `lo_supplycost` int(11) NOT NULL COMMENT "",
  `lo_tax` int(11) NOT NULL COMMENT "",
  `lo_commitdate` int(11) NOT NULL COMMENT "",
  `lo_shipmode` varchar(11) NOT NULL COMMENT ""
) ENGINE=OLAP
DUPLICATE KEY(`lo_orderkey`)
PARTITION BY RANGE(`lo_orderdate`)
(PARTITION p1 VALUES [("-2147483648"), ("19930101")),
PARTITION p2 VALUES [("19930101"), ("19940101")),
PARTITION p3 VALUES [("19940101"), ("19950101")),
PARTITION p4 VALUES [("19950101"), ("19960101")),
PARTITION p5 VALUES [("19960101"), ("19970101")),
PARTITION p6 VALUES [("19970101"), ("19980101")),
PARTITION p7 VALUES [("19980101"), ("19990101")))
DISTRIBUTED BY HASH(`lo_orderkey`) BUCKETS 1 PROPERTIES ("replication_num" = "1");
INSERT INTO lineorder VALUES (1, 1, 1, 1, 1, 19940101, '1-URGENT', 0, 10, 100, 1000, 1, 990, 50, 2, 19940105, 'AIR');
