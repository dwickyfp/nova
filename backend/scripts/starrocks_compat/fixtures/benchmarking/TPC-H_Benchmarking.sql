-- Empty TPC-H tables (the page's own DDL, 1 bucket) and the revenue0 view: the 22 queries come before the page's CREATE TABLE section, which drops and recreates them.
CREATE TABLE customer (
    c_custkey INT NOT NULL,
    c_name VARCHAR(25) NOT NULL,
    c_address VARCHAR(40) NOT NULL,
    c_nationkey INT NOT NULL,
    c_phone VARCHAR(15) NOT NULL,
    c_acctbal DECIMAL(15, 2) NOT NULL,
    c_mktsegment VARCHAR(10) NOT NULL,
    c_comment VARCHAR(117) NOT NULL
) DUPLICATE KEY(c_custkey) DISTRIBUTED BY HASH(c_custkey) BUCKETS 1
PROPERTIES ("replication_num" = "1");
CREATE TABLE lineitem (
    l_shipdate DATE NOT NULL,
    l_orderkey INT NOT NULL,
    l_linenumber INT NOT NULL,
    l_partkey INT NOT NULL,
    l_suppkey INT NOT NULL,
    l_quantity DECIMAL(15, 2) NOT NULL,
    l_extendedprice DECIMAL(15, 2) NOT NULL,
    l_discount DECIMAL(15, 2) NOT NULL,
    l_tax DECIMAL(15, 2) NOT NULL,
    l_returnflag VARCHAR(1) NOT NULL,
    l_linestatus VARCHAR(1) NOT NULL,
    l_commitdate DATE NOT NULL,
    l_receiptdate DATE NOT NULL,
    l_shipinstruct VARCHAR(25) NOT NULL,
    l_shipmode VARCHAR(10) NOT NULL,
    l_comment VARCHAR(44) NOT NULL
) DUPLICATE KEY(l_shipdate, l_orderkey) DISTRIBUTED BY HASH(l_orderkey) BUCKETS 1
PROPERTIES ("replication_num" = "1");
CREATE TABLE nation (
    n_nationkey INT NOT NULL,
    n_name VARCHAR(25) NOT NULL,
    n_regionkey INT NOT NULL,
    n_comment VARCHAR(152) NULL
) DUPLICATE KEY(n_nationkey) DISTRIBUTED BY HASH(n_nationkey) BUCKETS 1
PROPERTIES ("replication_num" = "1");
CREATE TABLE orders (
    o_orderkey INT NOT NULL,
    o_orderdate DATE NOT NULL,
    o_custkey INT NOT NULL,
    o_orderstatus VARCHAR(1) NOT NULL,
    o_totalprice DECIMAL(15, 2) NOT NULL,
    o_orderpriority VARCHAR(15) NOT NULL,
    o_clerk VARCHAR(15) NOT NULL,
    o_shippriority INT NOT NULL,
    o_comment VARCHAR(79) NOT NULL
) DUPLICATE KEY(o_orderkey, o_orderdate) DISTRIBUTED BY HASH(o_orderkey) BUCKETS 1
PROPERTIES ("replication_num" = "1");
CREATE TABLE part (
    p_partkey INT NOT NULL,
    p_name VARCHAR(55) NOT NULL,
    p_mfgr VARCHAR(25) NOT NULL,
    p_brand VARCHAR(10) NOT NULL,
    p_type VARCHAR(25) NOT NULL,
    p_size INT NOT NULL,
    p_container VARCHAR(10) NOT NULL,
    p_retailprice DECIMAL(15, 2) NOT NULL,
    p_comment VARCHAR(23) NOT NULL
) DUPLICATE KEY(p_partkey) DISTRIBUTED BY HASH(p_partkey) BUCKETS 1
PROPERTIES ("replication_num" = "1");
CREATE TABLE partsupp (
    ps_partkey INT NOT NULL,
    ps_suppkey INT NOT NULL,
    ps_availqty INT NOT NULL,
    ps_supplycost DECIMAL(15, 2) NOT NULL,
    ps_comment VARCHAR(199) NOT NULL
) DUPLICATE KEY(ps_partkey) DISTRIBUTED BY HASH(ps_partkey) BUCKETS 1
PROPERTIES ("replication_num" = "1");
CREATE TABLE region (
    r_regionkey INT NOT NULL,
    r_name VARCHAR(25) NOT NULL,
    r_comment VARCHAR(152)
) DUPLICATE KEY(r_regionkey) DISTRIBUTED BY HASH(r_regionkey) BUCKETS 1
PROPERTIES ("replication_num" = "1");
CREATE TABLE supplier (
    s_suppkey INT NOT NULL,
    s_name VARCHAR(25) NOT NULL,
    s_address VARCHAR(40) NOT NULL,
    s_nationkey INT NOT NULL,
    s_phone VARCHAR(15) NOT NULL,
    s_acctbal DECIMAL(15, 2) NOT NULL,
    s_comment VARCHAR(101) NOT NULL
) DUPLICATE KEY(s_suppkey) DISTRIBUTED BY HASH(s_suppkey) BUCKETS 1
PROPERTIES ("replication_num" = "1");
-- Query 15 reads revenue0, which the page defines later in its DDL section.
CREATE VIEW revenue0 (supplier_no, total_revenue) AS
SELECT l_suppkey, sum(l_extendedprice * (1 - l_discount))
FROM lineitem
WHERE l_shipdate >= date '1996-01-01' AND l_shipdate < date '1996-01-01' + interval '3' month
GROUP BY l_suppkey;
