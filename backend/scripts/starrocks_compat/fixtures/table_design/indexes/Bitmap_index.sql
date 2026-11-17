-- lineorder_partial, lineorder_without_index and lineorder_with_index as defined on the page (its CREATE blocks sit in list items), with a few rows.
CREATE TABLE `lineorder_partial` (
  `lo_orderkey` int(11) NOT NULL COMMENT "",
  `lo_orderdate` int(11) NOT NULL COMMENT "",
  `lo_orderpriority` varchar(16) NOT NULL COMMENT "",
  `lo_quantity` int(11) NOT NULL COMMENT "",
  `lo_revenue` int(11) NOT NULL COMMENT "",
   INDEX lo_orderdate_index (lo_orderdate) USING BITMAP
) ENGINE=OLAP
DUPLICATE KEY(`lo_orderkey`)
DISTRIBUTED BY HASH(`lo_orderkey`) BUCKETS 1
PROPERTIES ("replication_num" = "1");
CREATE TABLE `lineorder_without_index` (
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
DISTRIBUTED BY HASH(`lo_orderkey`) BUCKETS 1
PROPERTIES ("replication_num" = "1");
CREATE TABLE `lineorder_with_index` (
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
  `lo_shipmode` varchar(11) NOT NULL COMMENT "",
  INDEX i_shipmode (`lo_shipmode`) USING BITMAP,
  INDEX i_quantity (`lo_quantity`) USING BITMAP,
  INDEX i_discount (`lo_discount`) USING BITMAP,
  INDEX i_orderdate (`lo_orderdate`) USING BITMAP,
  INDEX i_tax (`lo_tax`) USING BITMAP,
  INDEX i_partkey (`lo_partkey`) USING BITMAP
) ENGINE=OLAP
DUPLICATE KEY(`lo_orderkey`)
DISTRIBUTED BY HASH(`lo_orderkey`) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO lineorder_partial VALUES
  (1, 19940101, '1-URGENT', 10, 1000),
  (2, 19940102, '2-HIGH', 20, 2000);
INSERT INTO lineorder_without_index VALUES
  (1, 1, 7, 10000, 3, 19940101, '1-URGENT', 0, 10, 100, 500, 9, 900, 60, 8, 19940110, 'MAIL'),
  (2, 1, 8, 20000, 4, 19940102, '2-HIGH', 0, 25, 200, 700, 5, 1900, 80, 2, 19940111, 'TRUCK'),
  (3, 2, 9, 10000, 5, 19940103, '3-MEDIUM', 0, 10, 300, 900, 9, 2700, 90, 8, 19940112, 'MAIL');
INSERT INTO lineorder_with_index SELECT * FROM lineorder_without_index;
