# Table models, schema changes and DML

Keywords: sql-tables, create table, tabel, primary key, duplicate key, aggregate key, insert, update, delete, upsert, merge, ctas, alter table, truncate, partition, index.

Use a Primary Key table for mutable records. Key columns must appear first in
schema order. Duplicate Key tables retain duplicate rows; Aggregate Key tables
combine values with declared aggregation functions. These are data models, not
ordinary uniqueness constraints added to the same table.

```sql
CREATE TABLE analytics.accounts (
  account_id BIGINT NOT NULL,
  name VARCHAR(200),
  balance DECIMAL(18,2)
) PRIMARY KEY(account_id)
DISTRIBUTED BY HASH(account_id) BUCKETS 4;
INSERT INTO analytics.accounts (account_id, name, balance)
VALUES (1, 'Example account', 10.00);
UPDATE analytics.accounts SET balance = 20.00 WHERE account_id = 1;
DELETE FROM analytics.accounts WHERE account_id = 1;
```

INSERT into a Primary Key table upserts a key; since 3.3.1 an explicit target
column list performs a partial update, whereas omitting it performs full upsert.
UPDATE affects matching existing rows in Primary Key tables; it does not insert
a missing row. DELETE also supports other key models with model-specific
predicate restrictions. Inspect SHOW CREATE TABLE before choosing mutation
semantics. Use explicit INSERT target columns and align SELECT expressions.
Avoid MySQL ON DUPLICATE KEY UPDATE and Snowflake MERGE templates unless the
installed engine version and grammar explicitly support the requested form.

CTAS: CREATE TABLE analytics.copy_accounts AS SELECT * FROM analytics.accounts;
CTAS creates and loads; CREATE TABLE analytics.empty_accounts LIKE
analytics.accounts copies table structure. Inspect key/distribution properties
after CTAS instead of assuming it preserves the source model.

ALTER TABLE analytics.accounts ADD COLUMN status VARCHAR(20);
ALTER TABLE analytics.accounts RENAME accounts_v2;
SHOW ALTER TABLE COLUMN FROM analytics;
Schema changes may be asynchronous; submitted is not completed.

DROP TABLE IF EXISTS analytics.accounts; TRUNCATE TABLE analytics.accounts;
Both remove data and require explicit confirmation in Nova. DELETE without a
WHERE also needs confirmation. Never use a destructive workaround for a syntax
error. A multi-statement batch is not a transaction and stops at its first error.

Daily expression partitioning is `PARTITION BY date_trunc('day', event_date)`;
partitions are created during loading. A fixed `PARTITION BY RANGE(event_date)`
with START/END/EVERY defines bounded ranges and is a different choice. Do not
replace an expression-partition request with arbitrary date boundaries.

Partitioning, hash/random distribution, sort keys, bloom filters, bitmap/inverted
indexes and replication are engine-specific choices. Ask for the access pattern
and inspect cluster topology before selecting values. replication_num=1 is a
single-node development choice, not a production default. Local CRUD state uses
NOVA_SYSTEM Primary Key tables; never create another persistence database engine.

Implementation references: app/sql_dialect/grammar/StarRocks.g4;
https://docs.starrocks.io/docs/sql-reference/sql-statements/table_bucket_part_index/CREATE_TABLE/;
https://docs.starrocks.io/docs/sql-reference/sql-statements/table_bucket_part_index/UPDATE/;
https://docs.starrocks.io/docs/sql-reference/sql-statements/loading_unloading/INSERT/;
https://docs.starrocks.io/docs/table_design/data_distribution/expression_partitioning/.
