# Module 07: Function Manager

> Browse, search, and manage built-in functions, UDFs, SQL UDFs, and AI functions.

---

## Built-in Functions

### Function Categories

| Category | Examples |
|----------|---------|
| **Aggregate** | `sum`, `avg`, `count`, `min`, `max`, `min_n`, `max_n`, `group_concat`, `STRING_AGG`, `percentile_approx`, `approx_count_distinct`, `bitmap_union`, `hll_union` |
| **Array** | `array_map`, `array_filter`, `array_sort` (with lambda), `array_top_n`, `arrays_zip`, `array_agg`, `array_flatten`, `array_contains`, `array_position`, `array_slice`, `array_length`, `array_concat` |
| **JSON** | `json_query`, `json_value`, `json_exists`, `json_each`, `json_keys`, `json_object`, `json_array`, `json_pretty`, `json_set`, `is_json_scalar`, `get_json_scalar`, `parse_json`, `to_json` |
| **String** | `concat`, `substr`, `length`, `upper`, `lower`, `trim`, `ltrim`, `rtrim`, `replace`, `split_part`, `regexp_replace`, `regexp_extract`, `regexp_position`, `initcap`, `md5`, `sha2`, `to_base64`, `from_base64` |
| **Date/Time** | `current_date`, `current_timestamp`, `date_trunc`, `date_add`, `date_sub`, `datediff`, `date_format`, `year`, `month`, `day`, `hour`, `minute`, `second`, `unix_timestamp`, `from_unixtime`, `str_to_date`, `sec_to_time` |
| **Math** | `abs`, `ceil`, `floor`, `round`, `sqrt`, `pow`, `log`, `exp`, `rand`, `sign`, `truncate`, `pi` |
| **Conditional** | `if`, `ifnull`, `nullif`, `coalesce`, `case when`, `nullif` |
| **Type Conversion** | `cast`, `convert`, `STRUCT_CAST_BY_NAME` |
| **Map** | `map_keys`, `map_values`, `map_from_arrays`, `sum_map`, `map_filter`, `map_apply` |
| **Struct** | `row`, `named_struct`, `struct_element` |
| **Binary** | `to_binary`, `from_binary`, `hex`, `unhex` |
| **Bitmap** | `bitmap_count`, `bitmap_and`, `bitmap_or`, `bitmap_xor`, `bitmap_to_array`, `to_bitmap` |
| **Hash** | `murmur_hash3_32`, `xx_hash3_64`, `city_hash64` |
| **Cryptographic** | `aes_encrypt`, `aes_decrypt`, `md5`, `sha1`, `sha2` |
| **Pattern Matching** | `like`, `rlike`, `regexp`, `regexp_extract_all` |
| **Spatial** | `st_point`, `st_distance`, `st_contains`, `st_as_text`, `st_from_wkt` |
| **Percentile** | `percentile_approx`, `percentile_cont`, `percentile_disc` |
| **Utility** | `uuid`, `uuid_v7`, `inet_aton`, `inet_ntoa`, `get_json_object`, `sleep`, `raise_error`, `query_id` |
| **Window** | `row_number`, `rank`, `dense_rank`, `ntile`, `lead`, `lag`, `first_value`, `last_value`, `nth_value`, `sum/avg/count/min/max OVER (...)` |
| **Table Functions** | `FILES()`, `unnest()`, `generate_series()` |
| **AI Functions** (v4.1) | `ai_query(prompt, config)` — Call external LLM from SQL |
| **Meta Functions** | `inspect_task_runs()`, `query_id()`, `last_query_id()` |

### Window Functions

| Function | Description |
|----------|-------------|
| `ROW_NUMBER()` | Sequential row number |
| `RANK()` | Rank with gaps |
| `DENSE_RANK()` | Rank without gaps |
| `NTILE(n)` | Divide into n buckets |
| `LEAD(col, n)` | Next row value |
| `LAG(col, n)` | Previous row value |
| `FIRST_VALUE(col)` | First value in frame |
| `LAST_VALUE(col)` | Last value in frame |
| `NTH_VALUE(col, n)` | Nth value in frame |
| `SUM/AVG/COUNT/MIN/MAX OVER` | Aggregate over window |
| `COUNT(DISTINCT) OVER` (v4.1) | Distinct aggregate over window |
| `ARRAY types in window` (v4.1) | lead/lag/first_value/last_value with ARRAY |

### Lambda Expressions

```sql
-- array_sort with lambda comparator (v4.1)
SELECT array_sort(arr, (x, y) -> x - y);

-- array_map
SELECT array_map(x -> x * 2, arr);

-- array_filter
SELECT array_filter(x -> x > 10, arr);
```

---

## User-Defined Functions

### SQL UDF (v4.1 — Lightweight)

```sql
-- Create
CREATE FUNCTION format_name(name STRING)
RETURNS concat('USER_', upper(name));

-- Create global
CREATE GLOBAL FUNCTION format_date(dt DATETIME)
RETURNS concat(year(dt), '-', lpad(month(dt), 2, '0'));

-- Nested UDF
CREATE FUNCTION calculate(a INT, b INT)
RETURNS a * b + 1;

-- Use
SELECT format_name('alice');  -- USER_ALICE
SELECT format_date(created_at) FROM orders;
```

**Characteristics:**
- Expression-based, expanded at query optimization time
- No external dependency
- Supports nested calls
- Supports GLOBAL scope

### Java UDF

```sql
-- Scalar function
CREATE FUNCTION my_func(INT, INT)
RETURNS INT
PROPERTIES (
    "symbol" = "com.example.MyFunc",
    "type" = "StarRocks",
    "file" = "hdfs://path/to/udf.jar"
);

-- Aggregate function
CREATE AGGREGATE FUNCTION my_agg(BIGINT)
RETURNS BIGINT
PROPERTIES (...);

-- Table function
CREATE TABLE FUNCTION my_tf(INT)
RETURNS TABLE(x INT, y STRING)
PROPERTIES (...);
```

**v4.1 Enhancements:**
- STRUCT args/returns for UDAF/UDTF
- Nested ARRAY/MAP types
- DATE/DATETIME, DECIMAL types
- Varargs support
- Scalar UDFs: STRUCT arguments
- UDAFs: loaded once, reused across queries (reduced overhead)

### Python UDF

```python
# v4.1: supports nested ARRAY/MAP types
@StarRocksPythonUDF
def my_func(x: int) -> str:
    return f"value: {x}"
```

### Load UDFs from S3

```sql
CREATE FUNCTION my_udf(INT)
RETURNS INT
PROPERTIES (
    "file" = "s3://bucket/udf.jar",
    "symbol" = "com.example.MyFunc",
    "type" = "StarRocks"
);
```

---

## AI Functions (v4.1)

```sql
-- Call external LLM from SQL
SELECT ai_query(
    concat('Classify sentiment: ', review_text),
    '{"model": "gpt-4o-mini", "endpoint_url": "http://llm:11434/v1"}'
) AS sentiment
FROM reviews;
```

**Features:**
- OpenAI-compatible API endpoints
- Async execution with thread pool
- LRU cache for repeated prompts
- Dynamic prompt construction from column values
- Works with any OpenAI-compatible LLM (OpenAI, Azure, Ollama, vLLM, etc.)
