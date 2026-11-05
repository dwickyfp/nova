# Snowflake JDBC catalog acceptance (2026-11-05)

Live acceptance of `patches/starrocks/4.1.4-jdbc-snowflake.patch` on the governed local stack
(compose project `docker`). The FE and BE images were built from `feat/nova-catalog-phase4`. The
Snowflake side was a trial account in `ap-southeast-3` (AWS).

## Setup

The Snowflake fixture lives in database `NOVA_CATALOG_TEST`:

- Schema `SALES` holds table `ORDERS`: 4 rows, with one column of each mapped type plus
  `"Mixed Case"`.
- Schema `SALES_EU` holds table `ORDER_ITEMS`.
- Schema `SALESXEU` holds table `DECOY`, a wildcard-escaping trap for `SALES_EU`.

A read-only role `NOVA_READER` and a `TYPE=SERVICE` user `NOVA_SVC` with key-pair auth read the
fixture. No password was used.

The catalog was created like this:

```sql
CREATE EXTERNAL CATALOG sf_phase4 PROPERTIES (
  'type'='jdbc',
  'driver_class'='net.snowflake.client.jdbc.SnowflakeDriver',
  'driver_url'='file:///opt/starrocks/drivers/snowflake-jdbc-3.28.0.jar',
  'jdbc_uri'='jdbc:snowflake://<account>.snowflakecomputing.com/?db=NOVA_CATALOG_TEST&warehouse=NOVA_TEST_WH&role=NOVA_READER&authenticator=SNOWFLAKE_JWT&private_key_file=<key path on FE and BE>&JDBC_QUERY_RESULT_FORMAT=JSON',
  'user'='NOVA_SVC', 'password'='');
```

## Results

| Check | Result |
| --- | --- |
| FE image build runs `SnowflakeSchemaResolverTest` | 12/12 passed |
| `./patches/starrocks/verify.sh` | Applies cleanly to `4a9848ed` |
| `CREATE EXTERNAL CATALOG` with the bundled driver | Created. The FE loaded the driver from `file://` in an isolated class loader. |
| `SHOW DATABASES` | `PUBLIC`, `SALES`, `SALES_EU`, `SALESXEU`. `INFORMATION_SCHEMA` is hidden. |
| `SHOW TABLES FROM sf_phase4.SALES_EU` | Only `ORDER_ITEMS`, so `_` was escaped and `DECOY` did not leak |
| `DESC sf_phase4.SALES.ORDERS` | See the mapped types below. The column comment `order key` is kept. |
| `SELECT *` | All 4 rows. `ARRAY_CONSTRUCT('a','b')` arrives as `["a", "b"]`, and `hex(RAW)` returns `CAFE`. |
| BE driver fetch | The BE loaded `file:///opt/starrocks/drivers/...`. Nothing was downloaded. |
| `EXPLAIN` filter pushdown | Shown below |
| Aggregation | `GROUP BY` runs in StarRocks and is not pushed, as expected |
| Quoted mixed-case column | `` `Mixed Case` = 'mc-2' `` returns one row |

`DESC sf_phase4.SALES.ORDERS` returned these types:

| Column | Mapped type |
| --- | --- |
| `NUMBER(38,0)` | `BIGINT` |
| `NUMBER(12,2)` | `DECIMAL(12,2)` (`9999999999.99` read exactly) |
| `FLOAT` | `DOUBLE` |
| `BOOLEAN` | `BOOLEAN` |
| `DATE` | `DATE` |
| `TIME` | `TIME` |
| `TIMESTAMP_*` | `DATETIME` |
| `VARIANT`, `ARRAY`, `OBJECT` | `JSON` |
| `BINARY` | `VARBINARY` |
| `VARCHAR` | `VARCHAR(16777216)` |

The `EXPLAIN` query was:

```sql
SELECT order_id, region, amount FROM sf_phase4.SALES.ORDERS
WHERE region = 'APAC' AND amount > 80 AND order_date >= '2026-01-01' LIMIT 5;
```

The scan node pushed all three filters and the `LIMIT` into the remote query:

```
0:SCAN JDBC
   TABLE: "SALES"."ORDERS"
   QUERY: SELECT "ORDER_ID", "REGION", "AMOUNT", "ORDER_DATE" FROM "SALES"."ORDERS"
          WHERE ("REGION" = 'APAC') AND ("AMOUNT" > 80E0) AND ("ORDER_DATE" >= '2026-01-01')
   limit: 5
```

The query wrote lower-case column names, yet the remote SQL uses the stored upper-case names in
quotes.

### Time zones

Snowflake's session `TIMEZONE` defaults to `America/Los_Angeles`, so `TIMESTAMP_LTZ` came back
in that zone: `2026-01-15 10:30 +07:00` read as `2026-01-14 19:30`. Adding
`TIMEZONE=Asia/Jakarta`, the engine's `time_zone`, to the URI returned `10:30`. `TIMESTAMP_TZ`
returns its stored wall-clock time without the offset: `23:59:59 +09:00` reads as `23:59:59`.
`TIMESTAMP_NTZ` is exact.

### Ranger, non-root role

The test used StarRocks user `sf_phase4_user` with default role `sf_phase4_reader`, synced to
Ranger as a user and a role. It had three Ranger policies on `sf_phase4.SALES.ORDERS`:

- `select`;
- a row filter `REGION = 'APAC'`;
- a `CUSTOM` mask on `CUSTOMER_EMAIL`: `concat('***@', split_part(CUSTOMER_EMAIL, '@', 2))`.

| Check | Result |
| --- | --- |
| `SELECT` as `sf_phase4_reader` | Rows 1 and 3 (APAC) only, with `CUSTOMER_EMAIL` shown as `***@example.com` |
| `WHERE region = 'EMEA'` | 0 rows |
| `WHERE region = 'EMEA' OR 1=1` | Still only the APAC rows, still masked |
| `EXPLAIN` | The Ranger row filter is pushed to Snowflake (`... AND ("REGION" = 'APAC')`), and the mask runs in StarRocks |
| `SELECT` on `SALES_EU.ORDER_ITEMS`, which has no policy | Denied: `SELECT privilege(s) on COLUMN ITEM_ID` |
| `sf_phase4.sales.orders` in lower case | `Unknown database 'sales'`. There is no second spelling around the policy. |
| `SET ROLE NONE` then `SELECT` | Refused: `Ranger active-role mode requires exactly one active StarRocks role` |
| `SHOW DATABASES FROM sf_phase4` as the reader | Only `SALES` |
| `SHOW CREATE CATALOG` as root and as the reader | `password` is omitted; `jdbc_uri` is printed in full |

All StarRocks and Ranger acceptance objects were removed afterwards. The Snowflake fixture
(`NOVA_CATALOG_TEST`, `NOVA_TEST_WH`, `NOVA_READER`, `NOVA_SVC`) was left in the account for the
Nova connector work.

## Not covered here

- The Nova `snowflake` connector: creating the catalog from a `CATALOG_CONNECTIONS` row,
  delivering key material to FE/BE, and harvesting `ACCOUNT_USAGE` comments and tags. It
  depends on phase 2.
- A semantic view over a Snowflake table and an agent answer through it. This needs the
  connector, because the catalog must be registered in Nova Catalog first.
