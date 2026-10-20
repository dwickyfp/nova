# Katalog probe SQL Nova

49 record terdiri dari 48 probe SQL dan 1 identitas engine. SUCCESS menunjukkan statement berhasil, bukan assertion compatibility lengkap. ERROR mempertahankan batas syntax/engine dan kesalahan probe awal; retest dijelaskan dalam laporan. Durasi client tidak dipakai sebagai benchmark.

## cte_values

Status: **SUCCESS**. Kategori runner: `common`.

```sql
WITH v(id,amount) AS (SELECT 1,10 UNION ALL SELECT 2,20) SELECT SUM(amount) FROM v
```

Kolom: `['sum(amount)']`. Hasil:

```json
[
  [
    30
  ]
]
```

## snowflake_values

Status: **ERROR**. Kategori runner: `exact`.

```sql
WITH v(id,amount) AS (SELECT column1,column2 FROM VALUES (1,10),(2,20)) SELECT SUM(amount) FROM v
```

Error:

```text
Invalid SQL: 1:50 Unexpected or missing SQL token near 'VALUES'
```

## qualify

Status: **ERROR**. Kategori runner: `exact`.

```sql
SELECT id, ROW_NUMBER() OVER (ORDER BY id) AS rn FROM (SELECT 1 AS id UNION ALL SELECT 2) v QUALIFY rn=1
```

Error:

```text
SQL error: (1064) Getting syntax error. Detail message: Can't support result other than column..
```

## group_by_all

Status: **ERROR**. Kategori runner: `exact`.

```sql
SELECT city,SUM(amount) FROM (SELECT 'Jakarta' AS city,10 AS amount UNION ALL SELECT 'Bandung',20) v GROUP BY ALL
```

Error:

```text
Invalid SQL: 1:110 Unexpected or missing SQL token near 'ALL'
```

## group_by_columns

Status: **SUCCESS**. Kategori runner: `alternative`.

```sql
SELECT city,SUM(amount) FROM (SELECT 'Jakarta' AS city,10 AS amount UNION ALL SELECT 'Bandung',20) v GROUP BY city ORDER BY city
```

Kolom: `['city', 'sum(amount)']`. Hasil:

```json
[
  [
    "Bandung",
    20
  ],
  [
    "Jakarta",
    10
  ]
]
```

## select_exclude

Status: **SUCCESS**. Kategori runner: `exact`.

```sql
SELECT * EXCLUDE (b) FROM (SELECT 1 AS a,2 AS b) v
```

Kolom: `['a']`. Hasil:

```json
[
  [
    1
  ]
]
```

## select_replace

Status: **ERROR**. Kategori runner: `exact`.

```sql
SELECT * REPLACE (a+1 AS a) FROM (SELECT 1 AS a,2 AS b) v
```

Error:

```text
Invalid SQL: 1:9 Unexpected or missing SQL token near 'REPLACE'
```

## select_ilike

Status: **ERROR**. Kategori runner: `exact`.

```sql
SELECT * ILIKE 'A%' FROM (SELECT 1 AS aa,2 AS bb) v
```

Error:

```text
Invalid SQL: 1:9 Unexpected or missing SQL token
```

## trailing_comma

Status: **ERROR**. Kategori runner: `exact`.

```sql
SELECT 1 AS a, 2 AS b,
```

Error:

```text
Invalid SQL: 1:22 Unexpected or missing SQL token at end of input
```

## alias_reuse

Status: **SUCCESS**. Kategori runner: `exact`.

```sql
SELECT 2 AS a,a+1 AS b
```

Kolom: `['a', 'b']`. Hasil:

```json
[
  [
    2,
    3
  ]
]
```

## distinct_from

Status: **ERROR**. Kategori runner: `exact`.

```sql
SELECT NULL IS NOT DISTINCT FROM NULL
```

Error:

```text
Invalid SQL: 1:19 Unexpected or missing SQL token near 'DISTINCT'
```

## equal_null

Status: **ERROR**. Kategori runner: `exact`.

```sql
SELECT EQUAL_NULL(NULL,NULL)
```

Error:

```text
SQL error: (1064) Getting analyzing error from line 1, column 7 to line 1, column 27. Detail message: No matching function with signature: equal_null(NULL_TYPE, NULL_TYPE).
```

## null_safe_mysql

Status: **SUCCESS**. Kategori runner: `alternative`.

```sql
SELECT NULL <=> NULL
```

Kolom: `['NULL <=> NULL']`. Hasil:

```json
[
  [
    1
  ]
]
```

## try_to_number

Status: **ERROR**. Kategori runner: `exact`.

```sql
SELECT TRY_TO_NUMBER('bad')
```

Error:

```text
SQL error: (1064) Getting analyzing error from line 1, column 7 to line 1, column 26. Detail message: No matching function with signature: try_to_number(varchar).
```

## cast_bad

Status: **SUCCESS**. Kategori runner: `behavior`.

```sql
SELECT CAST('bad' AS INT)
```

Kolom: `["CAST('bad' AS INT)"]`. Hasil:

```json
[
  [
    null
  ]
]
```

## iff

Status: **ERROR**. Kategori runner: `exact`.

```sql
SELECT IFF(TRUE,'yes','no')
```

Error:

```text
SQL error: (1064) Getting analyzing error from line 1, column 7 to line 1, column 26. Detail message: No matching function with signature: iff(boolean, varchar, varchar).
```

## if

Status: **SUCCESS**. Kategori runner: `alternative`.

```sql
SELECT IF(TRUE,'yes','no')
```

Kolom: `["if(TRUE, 'yes', 'no')"]`. Hasil:

```json
[
  [
    "yes"
  ]
]
```

## parse_json

Status: **SUCCESS**. Kategori runner: `exact`.

```sql
SELECT PARSE_JSON('{"x":7}')
```

Kolom: `['parse_json(\'{"x":7}\')']`. Hasil:

```json
[
  [
    "{\"x\": 7}"
  ]
]
```

## snowflake_json_path

Status: **ERROR**. Kategori runner: `exact`.

```sql
SELECT PARSE_JSON('{"x":7}'):x::INT
```

Error:

```text
Invalid SQL: 1:28 Unexpected or missing SQL token near ':'
```

## starrocks_json_path

Status: **SUCCESS**. Kategori runner: `alternative`.

```sql
SELECT CAST(PARSE_JSON('{"x":7}')->'x' AS INT)
```

Kolom: `['CAST((parse_json(\'{"x":7}\')->\'x\') AS INT)']`. Hasil:

```json
[
  [
    7
  ]
]
```

## flatten

Status: **ERROR**. Kategori runner: `exact`.

```sql
SELECT value FROM TABLE(FLATTEN(INPUT=>PARSE_JSON('[1,2]')))
```

Error:

```text
SQL error: (1064) Getting analyzing error. Detail message: Unknown table function 'flatten(INPUT=>PARSE_JSON('[1,2]'))', the function doesn't support named arguments or has invalid arguments.
```

## unnest

Status: **ERROR**. Kategori runner: `alternative`.

```sql
SELECT x FROM UNNEST([1,2]) AS u(x)
```

Error:

```text
SQL error: (1064) Table function must be used with lateral join
```

## array_construct

Status: **ERROR**. Kategori runner: `exact`.

```sql
SELECT ARRAY_CONSTRUCT(1,2)
```

Error:

```text
SQL error: (1064) Getting analyzing error from line 1, column 7 to line 1, column 26. Detail message: No matching function with signature: array_construct(tinyint(4), tinyint(4)).
```

## array_literal

Status: **SUCCESS**. Kategori runner: `alternative`.

```sql
SELECT [1,2]
```

Kolom: `['[1, 2]']`. Hasil:

```json
[
  [
    "[1,2]"
  ]
]
```

## dateadd

Status: **ERROR**. Kategori runner: `exact`.

```sql
SELECT DATEADD('day',1,'2026-10-13'::DATE)
```

Error:

```text
Invalid SQL: 1:14 Unexpected or missing SQL token near '('
```

## date_add

Status: **SUCCESS**. Kategori runner: `alternative`.

```sql
SELECT DATE_ADD(CAST('2026-10-13' AS DATE),INTERVAL 1 DAY)
```

Kolom: `["date_add(CAST('2026-10-13' AS DATE), INTERVAL 1 DAY)"]`. Hasil:

```json
[
  [
    "2026-10-14 00:00:00"
  ]
]
```

## convert_timezone

Status: **ERROR**. Kategori runner: `exact`.

```sql
SELECT CONVERT_TIMEZONE('UTC','Asia/Jakarta','2026-10-13 00:00:00'::TIMESTAMP_NTZ)
```

Error:

```text
Invalid SQL: 1:23 Unexpected or missing SQL token near '('
```

## convert_tz

Status: **SUCCESS**. Kategori runner: `alternative`.

```sql
SELECT CONVERT_TZ('2026-10-13 00:00:00','UTC','Asia/Jakarta')
```

Kolom: `["convert_tz('2026-10-13 00:00:00', 'UTC', 'Asia/Jakarta')"]`. Hasil:

```json
[
  [
    "2026-10-13 07:00:00"
  ]
]
```

## recursive_cte

Status: **ERROR**. Kategori runner: `exact`.

```sql
WITH RECURSIVE n(v) AS (SELECT 1 UNION ALL SELECT v+1 FROM n WHERE v<5) SELECT SUM(v) FROM n
```

Error:

```text
SQL error: (1064) Getting analyzing error. Detail message: Recursive CTE is not supported.
```

## generator

Status: **ERROR**. Kategori runner: `exact`.

```sql
SELECT SEQ4() FROM TABLE(GENERATOR(ROWCOUNT=>3))
```

Error:

```text
SQL error: (1064) Getting analyzing error. Detail message: Unknown table function 'generator(ROWCOUNT=>3)', the function doesn't support named arguments or has invalid arguments.
```

## generate_series

Status: **SUCCESS**. Kategori runner: `alternative`.

```sql
SELECT * FROM TABLE(generate_series(0,2))
```

Kolom: `['generate_series']`. Hasil:

```json
[
  [
    0
  ],
  [
    1
  ],
  [
    2
  ]
]
```

## union_by_name

Status: **ERROR**. Kategori runner: `exact`.

```sql
SELECT 1 AS a,2 AS b UNION ALL BY NAME SELECT 3 AS b,4 AS a
```

Error:

```text
Invalid SQL: 1:31 Unexpected or missing SQL token near 'BY'
```

## pipe_operator

Status: **ERROR**. Kategori runner: `exact`.

```sql
SELECT 2 AS a ->> SELECT a*3 FROM $1
```

Error:

```text
Invalid SQL: 1:14 Unexpected or missing SQL token near '->'
```

## asof_join

Status: **ERROR**. Kategori runner: `exact`.

```sql
SELECT * FROM (SELECT 2 AS ts) a ASOF JOIN (SELECT 1 AS ts) b MATCH_CONDITION(a.ts>=b.ts)
```

Error:

```text
Invalid SQL: 1:62 Unexpected or missing SQL token
```

## pivot

Status: **ERROR**. Kategori runner: `exact`.

```sql
SELECT * FROM (SELECT 'a' AS k,1 AS v) PIVOT(SUM(v) FOR k IN ('a'))
```

Error:

```text
SQL error: (1248) Getting analyzing error. Detail message: Every derived table must have its own alias.
```

## unpivot

Status: **ERROR**. Kategori runner: `exact`.

```sql
SELECT * FROM (SELECT 1 AS a,2 AS b) UNPIVOT(v FOR k IN (a,b))
```

Error:

```text
Invalid SQL: 1:47 Unexpected or missing SQL token near 'FOR'
```

## rollup

Status: **ERROR**. Kategori runner: `common`.

```sql
SELECT id,SUM(amount),GROUPING(id) FROM (SELECT 1 AS id,10 AS amount UNION ALL SELECT 2,20) v GROUP BY ROLLUP(id) ORDER BY GROUPING(id),id
```

Error:

```text
SQL error: (1064) Getting analyzing error from line 1, column 123 to line 1, column 134. Detail message: ORDER BY clause cannot contain grouping.
```

## rows_window

Status: **SUCCESS**. Kategori runner: `common`.

```sql
SELECT id,SUM(amount) OVER(ORDER BY id ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) FROM (SELECT 1 AS id,10 AS amount UNION ALL SELECT 2,20) v ORDER BY id
```

Kolom: `['id', 'sum(amount) OVER (ORDER BY id ASC ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)']`. Hasil:

```json
[
  [
    1,
    10
  ],
  [
    2,
    30
  ]
]
```

## divide_zero

Status: **SUCCESS**. Kategori runner: `behavior`.

```sql
SELECT 1/0
```

Kolom: `['1 / 0']`. Hasil:

```json
[
  [
    null
  ]
]
```

## quoted_identifier

Status: **SUCCESS**. Kategori runner: `behavior`.

```sql
SELECT "MixedCase" FROM (SELECT 7 AS "MixedCase") v
```

Kolom: `["'MixedCase'"]`. Hasil:

```json
[
  [
    "MixedCase"
  ]
]
```

## backtick_identifier

Status: **SUCCESS**. Kategori runner: `alternative`.

```sql
SELECT `MixedCase` FROM (SELECT 7 AS `MixedCase`) v
```

Kolom: `['MixedCase']`. Hasil:

```json
[
  [
    7
  ]
]
```

## regexp_replace

Status: **SUCCESS**. Kategori runner: `common`.

```sql
SELECT REGEXP_REPLACE('a1b2','[0-9]','')
```

Kolom: `["regexp_replace('a1b2', '[0-9]', '')"]`. Hasil:

```json
[
  [
    "ab"
  ]
]
```

## explain

Status: **SUCCESS**. Kategori runner: `common`.

```sql
EXPLAIN SELECT 2+3 AS result
```

Kolom: `['Explain String']`. Hasil:

```json
[
  [
    "EXECUTE IN FE"
  ],
  [
    "PLAN FRAGMENT 0"
  ],
  [
    " OUTPUT EXPRS:2: expr"
  ],
  [
    "  PARTITION: UNPARTITIONED"
  ],
  [
    ""
  ],
  [
    "  RESULT SINK"
  ],
  [
    ""
  ],
  [
    "  1:Project"
  ],
  [
    "  |  <slot 2> : 5"
  ],
  [
    "  |  "
  ]
]
```

## qualify_native

Status: **SUCCESS**. Kategori runner: `alternative`.

```sql
SELECT id FROM (SELECT 1 AS id UNION ALL SELECT 2) v QUALIFY ROW_NUMBER() OVER(ORDER BY id)=1
```

Kolom: `['id']`. Hasil:

```json
[
  [
    1
  ]
]
```

## rollup_native

Status: **SUCCESS**. Kategori runner: `alternative`.

```sql
SELECT id,SUM(amount),GROUPING(id) AS g FROM (SELECT 1 AS id,10 AS amount UNION ALL SELECT 2,20) v GROUP BY ROLLUP(id) ORDER BY g,id
```

Kolom: `['id', 'sum(amount)', 'g']`. Hasil:

```json
[
  [
    1,
    10,
    0
  ],
  [
    2,
    20,
    0
  ],
  [
    null,
    30,
    1
  ]
]
```

## unnest_native

Status: **SUCCESS**. Kategori runner: `alternative`.

```sql
SELECT x FROM (SELECT [1,2] AS arr) v, UNNEST(v.arr) AS u(x)
```

Kolom: `['x']`. Hasil:

```json
[
  [
    1
  ],
  [
    2
  ]
]
```

## pivot_alias

Status: **ERROR**. Kategori runner: `retest`.

```sql
SELECT * FROM (SELECT 'a' AS k,1 AS v) x PIVOT(SUM(v) FOR k IN ('a')) AS p
```

Error:

```text
Invalid SQL: 1:70 Unexpected or missing SQL token near 'AS'
```

## match_recognize

Status: **ERROR**. Kategori runner: `exact`.

```sql
SELECT * FROM (SELECT 1 AS id,10 AS v UNION ALL SELECT 2,20) t MATCH_RECOGNIZE(ORDER BY id MEASURES FIRST(id) AS start_id ONE ROW PER MATCH PATTERN(A B) DEFINE A AS v>0,B AS v>0)
```

Error:

```text
Invalid SQL: 1:63 Unexpected or missing SQL token
```

## engine_identity

Status: **SUCCESS**. Kategori runner: `metadata`.

```sql
SELECT CURRENT_VERSION()
```

Kolom: `['current_version()']`. Hasil:

```json
[
  [
    "4.1.4-4a9848e"
  ]
]
```
