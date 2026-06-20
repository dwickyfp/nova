import logging
from typing import Optional

from app.core.database import db
from app.modules.functions.schemas import (
    BuiltInFunction,
    FunctionCategory,
    UDFCreate,
    UDFResponse,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Built-in functions reference – organised by category
# ---------------------------------------------------------------------------

BUILTIN_FUNCTIONS: dict[str, list[dict]] = {
    "Aggregate": [
        {"name": "COUNT", "signature": "COUNT([DISTINCT] expr)", "return_type": "BIGINT", "description": "Returns the number of rows, optionally distinct non-NULL values."},
        {"name": "SUM", "signature": "SUM([DISTINCT] expr)", "return_type": "DOUBLE/BIGINT", "description": "Returns the sum of expr across matching rows."},
        {"name": "AVG", "signature": "AVG([DISTINCT] expr)", "return_type": "DOUBLE", "description": "Returns the arithmetic mean of expr."},
        {"name": "MIN", "signature": "MIN(expr)", "return_type": "Same as input", "description": "Returns the minimum value of expr."},
        {"name": "MAX", "signature": "MAX(expr)", "return_type": "Same as input", "description": "Returns the maximum value of expr."},
        {"name": "GROUP_CONCAT", "signature": "GROUP_CONCAT([DISTINCT] expr [, SEPARATOR sep])", "return_type": "VARCHAR", "description": "Concatenates group values into a single string."},
        {"name": "APPROX_COUNT_DISTINCT", "signature": "APPROX_COUNT_DISTINCT(expr)", "return_type": "BIGINT", "description": "HyperLogLog-based approximate distinct count."},
        {"name": "BITMAP_UNION", "signature": "BITMAP_UNION(bitmap_column)", "return_type": "BITMAP", "description": "Aggregates bitmap values via union."},
    ],
    "Array": [
        {"name": "ARRAY", "signature": "ARRAY(v1, v2, ...)", "return_type": "ARRAY", "description": "Constructs an array from the given values."},
        {"name": "ARRAY_LENGTH", "signature": "ARRAY_LENGTH(arr)", "return_type": "INT", "description": "Returns the number of elements in the array."},
        {"name": "ARRAY_AGG", "signature": "ARRAY_AGG(expr)", "return_type": "ARRAY", "description": "Aggregate function that collects values into an array."},
        {"name": "ARRAY_CONTAINS", "signature": "ARRAY_CONTAINS(arr, value)", "return_type": "BOOLEAN", "description": "Returns true if the array contains the value."},
        {"name": "ARRAY_SORT", "signature": "ARRAY_SORT(arr)", "return_type": "ARRAY", "description": "Returns the array sorted in ascending order."},
        {"name": "ARRAY_DISTINCT", "signature": "ARRAY_DISTINCT(arr)", "return_type": "ARRAY", "description": "Removes duplicate elements from the array."},
        {"name": "ARRAY_CONCAT", "signature": "ARRAY_CONCAT(arr1, arr2, ...)", "return_type": "ARRAY", "description": "Concatenates multiple arrays into one."},
        {"name": "ARRAY_SLICE", "signature": "ARRAY_SLICE(arr, offset, length)", "return_type": "ARRAY", "description": "Returns a sub-array starting at offset with given length."},
    ],
    "JSON": [
        {"name": "JSON_OBJECT", "signature": "JSON_OBJECT(key1, val1, ...)", "return_type": "JSON", "description": "Creates a JSON object from key-value pairs."},
        {"name": "JSON_ARRAY", "signature": "JSON_ARRAY(v1, v2, ...)", "return_type": "JSON", "description": "Creates a JSON array from the given values."},
        {"name": "JSON_QUERY", "signature": "JSON_QUERY(json_doc, path)", "return_type": "JSON", "description": "Extracts a JSON value at the given path."},
        {"name": "JSON_VALUE", "signature": "JSON_VALUE(json_doc, path)", "return_type": "VARCHAR", "description": "Extracts a scalar value from JSON at the given path."},
        {"name": "JSON_EXISTS", "signature": "JSON_EXISTS(json_doc, path)", "return_type": "BOOLEAN", "description": "Returns true if the path exists in the JSON document."},
        {"name": "GET_JSON_STRING", "signature": "GET_JSON_STRING(json_str, path)", "return_type": "VARCHAR", "description": "Extracts a string value from a JSON string using a path."},
        {"name": "JSON_PARSE", "signature": "JSON_PARSE(str)", "return_type": "JSON", "description": "Parses a string into a JSON value."},
    ],
    "String": [
        {"name": "CONCAT", "signature": "CONCAT(s1, s2, ...)", "return_type": "VARCHAR", "description": "Concatenates two or more strings."},
        {"name": "LENGTH", "signature": "LENGTH(str)", "return_type": "INT", "description": "Returns the byte length of the string."},
        {"name": "CHAR_LENGTH", "signature": "CHAR_LENGTH(str)", "return_type": "INT", "description": "Returns the character length of the string."},
        {"name": "SUBSTR", "signature": "SUBSTR(str, pos [, len])", "return_type": "VARCHAR", "description": "Returns a substring starting at pos with optional length."},
        {"name": "UPPER", "signature": "UPPER(str)", "return_type": "VARCHAR", "description": "Converts the string to uppercase."},
        {"name": "LOWER", "signature": "LOWER(str)", "return_type": "VARCHAR", "description": "Converts the string to lowercase."},
        {"name": "TRIM", "signature": "TRIM([BOTH|LEADING|TRAILING] [remstr FROM] str)", "return_type": "VARCHAR", "description": "Removes leading/trailing whitespace or specified characters."},
        {"name": "REPLACE", "signature": "REPLACE(str, from_str, to_str)", "return_type": "VARCHAR", "description": "Replaces all occurrences of from_str with to_str."},
    ],
    "DateTime": [
        {"name": "NOW", "signature": "NOW()", "return_type": "DATETIME", "description": "Returns the current date and time."},
        {"name": "CURDATE", "signature": "CURDATE()", "return_type": "DATE", "description": "Returns the current date."},
        {"name": "DATE_FORMAT", "signature": "DATE_FORMAT(date, format)", "return_type": "VARCHAR", "description": "Formats a date according to the format string."},
        {"name": "DATE_ADD", "signature": "DATE_ADD(date, INTERVAL expr unit)", "return_type": "DATE/DATETIME", "description": "Adds a time interval to a date."},
        {"name": "DATE_DIFF", "signature": "DATE_DIFF(date1, date2)", "return_type": "INT", "description": "Returns the difference in days between two dates."},
        {"name": "UNIX_TIMESTAMP", "signature": "UNIX_TIMESTAMP([date])", "return_type": "BIGINT", "description": "Returns a Unix timestamp for the given date or current time."},
        {"name": "FROM_UNIXTIME", "signature": "FROM_UNIXTIME(timestamp [, format])", "return_type": "VARCHAR/DATETIME", "description": "Converts a Unix timestamp to a datetime or formatted string."},
        {"name": "DATE_TRUNC", "signature": "DATE_TRUNC(date, unit)", "return_type": "DATETIME", "description": "Truncates a date to the specified unit (year, month, day, etc.)."},
    ],
    "Math": [
        {"name": "ABS", "signature": "ABS(x)", "return_type": "Same as input", "description": "Returns the absolute value of x."},
        {"name": "CEIL", "signature": "CEIL(x)", "return_type": "BIGINT", "description": "Returns the smallest integer >= x."},
        {"name": "FLOOR", "signature": "FLOOR(x)", "return_type": "BIGINT", "description": "Returns the largest integer <= x."},
        {"name": "ROUND", "signature": "ROUND(x [, d])", "return_type": "DOUBLE", "description": "Rounds x to d decimal places (default 0)."},
        {"name": "POW", "signature": "POW(x, y)", "return_type": "DOUBLE", "description": "Returns x raised to the power of y."},
        {"name": "LOG", "signature": "LOG([base,] x)", "return_type": "DOUBLE", "description": "Returns the logarithm of x, optionally with a given base."},
        {"name": "MOD", "signature": "MOD(x, y)", "return_type": "Same as input", "description": "Returns the remainder of x divided by y."},
        {"name": "RAND", "signature": "RAND([seed])", "return_type": "DOUBLE", "description": "Returns a random floating-point value between 0 and 1."},
    ],
    "Conditional": [
        {"name": "IF", "signature": "IF(cond, true_val, false_val)", "return_type": "Same as values", "description": "Returns true_val if cond is true, otherwise false_val."},
        {"name": "COALESCE", "signature": "COALESCE(v1, v2, ...)", "return_type": "Same as input", "description": "Returns the first non-NULL argument."},
        {"name": "NULLIF", "signature": "NULLIF(expr1, expr2)", "return_type": "Same as input", "description": "Returns NULL if expr1 = expr2, otherwise expr1."},
        {"name": "IFNULL", "signature": "IFNULL(expr1, expr2)", "return_type": "Same as input", "description": "Returns expr2 if expr1 is NULL."},
        {"name": "CASE", "signature": "CASE WHEN cond THEN result ... [ELSE default] END", "return_type": "Same as results", "description": "Conditional expression with multiple branches."},
        {"name": "NVL", "signature": "NVL(expr1, expr2)", "return_type": "Same as input", "description": "Returns expr2 if expr1 is NULL, otherwise expr1."},
    ],
    "TypeConversion": [
        {"name": "CAST", "signature": "CAST(expr AS type)", "return_type": "Specified type", "description": "Converts expr to the specified data type."},
        {"name": "CONVERT", "signature": "CONVERT(expr, type)", "return_type": "Specified type", "description": "Converts expr to the specified type (alternative syntax)."},
        {"name": "TYPEOF", "signature": "TYPEOF(expr)", "return_type": "VARCHAR", "description": "Returns the data type name of the expression."},
        {"name": "TRY_CAST", "signature": "TRY_CAST(expr AS type)", "return_type": "Specified type or NULL", "description": "Like CAST but returns NULL on failure instead of error."},
        {"name": "HEX", "signature": "HEX(x)", "return_type": "VARCHAR", "description": "Converts a number or string to its hexadecimal representation."},
        {"name": "UNHEX", "signature": "UNHEX(str)", "return_type": "VARBINARY", "description": "Converts a hexadecimal string back to binary."},
    ],
    "Map": [
        {"name": "MAP", "signature": "MAP(k1, v1, k2, v2, ...)", "return_type": "MAP", "description": "Constructs a map from key-value pairs."},
        {"name": "MAP_KEYS", "signature": "MAP_KEYS(m)", "return_type": "ARRAY", "description": "Returns the keys of the map as an array."},
        {"name": "MAP_VALUES", "signature": "MAP_VALUES(m)", "return_type": "ARRAY", "description": "Returns the values of the map as an array."},
        {"name": "MAP_FROM_ARRAYS", "signature": "MAP_FROM_ARRAYS(keys_arr, vals_arr)", "return_type": "MAP", "description": "Creates a map from a keys array and a values array."},
        {"name": "ELEMENT_AT", "signature": "ELEMENT_AT(m, key)", "return_type": "Value type", "description": "Returns the value for the given key in the map."},
    ],
    "Struct": [
        {"name": "NAMED_STRUCT", "signature": "NAMED_STRUCT(name1, val1, ...)", "return_type": "STRUCT", "description": "Constructs a struct with named fields."},
        {"name": "STRUCT", "signature": "STRUCT(v1, v2, ...)", "return_type": "STRUCT", "description": "Constructs a struct from positional values."},
        {"name": "ROW", "signature": "ROW(v1, v2, ...)", "return_type": "STRUCT", "description": "Alias for STRUCT constructor."},
        {"name": "FIELD", "signature": "FIELD(struct, 'field_name')", "return_type": "Field type", "description": "Accesses a named field from a struct."},
        {"name": "STRUCT_INSERT", "signature": "STRUCT_INSERT(struct, field AS name)", "return_type": "STRUCT", "description": "Inserts or replaces a field in a struct."},
    ],
    "Binary": [
        {"name": "TO_BASE64", "signature": "TO_BASE64(str)", "return_type": "VARCHAR", "description": "Encodes a string to Base64."},
        {"name": "FROM_BASE64", "signature": "FROM_BASE64(str)", "return_type": "VARBINARY", "description": "Decodes a Base64-encoded string."},
        {"name": "LENGTHB", "signature": "LENGTHB(binary)", "return_type": "INT", "description": "Returns the byte length of a binary value."},
        {"name": "CONCAT_BINARY", "signature": "CONCAT_BINARY(b1, b2, ...)", "return_type": "VARBINARY", "description": "Concatenates binary values."},
        {"name": "SUBSTRING_BINARY", "signature": "SUBSTRING_BINARY(b, pos, len)", "return_type": "VARBINARY", "description": "Extracts a sub-binary starting at pos with given length."},
    ],
    "Bitmap": [
        {"name": "BITMAP_EMPTY", "signature": "BITMAP_EMPTY()", "return_type": "BITMAP", "description": "Returns an empty bitmap."},
        {"name": "BITMAP_HASH", "signature": "BITMAP_HASH(expr)", "return_type": "BITMAP", "description": "Hashes the expression into a bitmap value."},
        {"name": "BITMAP_UNION_COUNT", "signature": "BITMAP_UNION_COUNT(bitmap)", "return_type": "BIGINT", "description": "Returns the cardinality of the union of bitmaps."},
        {"name": "BITMAP_CONTAINS", "signature": "BITMAP_CONTAINS(bitmap, value)", "return_type": "BOOLEAN", "description": "Returns true if the bitmap contains the value."},
        {"name": "BITMAP_INTERSECT", "signature": "BITMAP_INTERSECT(bitmap1, bitmap2)", "return_type": "BITMAP", "description": "Returns the intersection of two bitmaps."},
        {"name": "BITMAP_AND", "signature": "BITMAP_AND(bitmap1, bitmap2)", "return_type": "BITMAP", "description": "Returns the bitwise AND of two bitmaps."},
        {"name": "BITMAP_OR", "signature": "BITMAP_OR(bitmap1, bitmap2)", "return_type": "BITMAP", "description": "Returns the bitwise OR of two bitmaps."},
        {"name": "BITMAP_XOR", "signature": "BITMAP_XOR(bitmap1, bitmap2)", "return_type": "BITMAP", "description": "Returns the bitwise XOR of two bitmaps."},
    ],
    "Hash": [
        {"name": "MD5", "signature": "MD5(str)", "return_type": "VARCHAR", "description": "Returns the MD5 hash of the string as hex."},
        {"name": "SHA1", "signature": "SHA1(str)", "return_type": "VARCHAR", "description": "Returns the SHA-1 hash of the string as hex."},
        {"name": "SHA2", "signature": "SHA2(str, hash_length)", "return_type": "VARCHAR", "description": "Returns the SHA-2 family hash (224, 256, 384, 512)."},
        {"name": "MURMUR_HASH3_32", "signature": "MURMUR_HASH3_32(expr)", "return_type": "INT", "description": "Returns a 32-bit MurmurHash3 of the expression."},
        {"name": "XX_HASH3_32", "signature": "XX_HASH3_32(expr)", "return_type": "INT", "description": "Returns a 32-bit xxHash3 of the expression."},
        {"name": "XX_HASH3_64", "signature": "XX_HASH3_64(expr)", "return_type": "BIGINT", "description": "Returns a 64-bit xxHash3 of the expression."},
    ],
    "Cryptographic": [
        {"name": "AES_ENCRYPT", "signature": "AES_ENCRYPT(str, key)", "return_type": "VARBINARY", "description": "Encrypts a string using AES with the given key."},
        {"name": "AES_DECRYPT", "signature": "AES_DECRYPT(encrypted, key)", "return_type": "VARCHAR", "description": "Decrypts AES-encrypted data with the given key."},
        {"name": "SM3", "signature": "SM3(str)", "return_type": "VARCHAR", "description": "Returns the SM3 hash of the string."},
        {"name": "SM4_ENCRYPT", "signature": "SM4_ENCRYPT(str, key)", "return_type": "VARBINARY", "description": "Encrypts a string using the SM4 algorithm."},
        {"name": "SM4_DECRYPT", "signature": "SM4_DECRYPT(encrypted, key)", "return_type": "VARCHAR", "description": "Decrypts SM4-encrypted data."},
    ],
    "PatternMatching": [
        {"name": "LIKE", "signature": "expr LIKE pattern", "return_type": "BOOLEAN", "description": "Returns true if expr matches the SQL LIKE pattern."},
        {"name": "REGEXP", "signature": "expr REGEXP pattern", "return_type": "BOOLEAN", "description": "Returns true if expr matches the regular expression."},
        {"name": "REGEXP_EXTRACT", "signature": "REGEXP_EXTRACT(str, pattern [, idx])", "return_type": "VARCHAR", "description": "Extracts a group from the string using a regex."},
        {"name": "REGEXP_REPLACE", "signature": "REGEXP_REPLACE(str, pattern, replacement)", "return_type": "VARCHAR", "description": "Replaces regex matches in the string."},
        {"name": "INSTR", "signature": "INSTR(str, substr)", "return_type": "INT", "description": "Returns the position of the first occurrence of substr in str."},
        {"name": "LOCATE", "signature": "LOCATE(substr, str [, pos])", "return_type": "INT", "description": "Returns the position of substr in str, optionally starting at pos."},
    ],
    "Spatial": [
        {"name": "ST_POINT", "signature": "ST_POINT(x, y)", "return_type": "GEOMETRY", "description": "Creates a point geometry from x, y coordinates."},
        {"name": "ST_DISTANCE_SPHERE", "signature": "ST_DISTANCE_SPHERE(pt1, pt2)", "return_type": "DOUBLE", "description": "Returns the spherical distance in meters between two points."},
        {"name": "ST_ASWKT", "signature": "ST_ASWKT(geom)", "return_type": "VARCHAR", "description": "Returns the Well-Known Text representation of a geometry."},
        {"name": "ST_GEOMFROMTEXT", "signature": "ST_GEOMFROMTEXT(wkt)", "return_type": "GEOMETRY", "description": "Creates a geometry from a WKT string."},
        {"name": "ST_CONTAINS", "signature": "ST_CONTAINS(geom1, geom2)", "return_type": "BOOLEAN", "description": "Returns true if geom1 spatially contains geom2."},
    ],
    "Percentile": [
        {"name": "PERCENTILE_APPROX", "signature": "PERCENTILE_APPROX(expr, p)", "return_type": "DOUBLE", "description": "Returns the approximate percentile value (0-1) of expr."},
        {"name": "PERCENTILE_ARRAY", "signature": "PERCENTILE_ARRAY(expr, percentiles_array)", "return_type": "ARRAY<DOUBLE>", "description": "Returns multiple approximate percentile values."},
        {"name": "MEDIAN", "signature": "MEDIAN(expr)", "return_type": "DOUBLE", "description": "Returns the median (50th percentile) of expr."},
        {"name": "QUANTILE_UNION", "signature": "QUANTILE_UNION(percentile_column)", "return_type": "PERCENTILE", "description": "Aggregates percentile values via union for later estimation."},
        {"name": "QUANTILE_PERCENT", "signature": "QUANTILE_PERCENT(percentile, p)", "return_type": "DOUBLE", "description": "Extracts the value at percentile p from an aggregated percentile."},
    ],
    "Utility": [
        {"name": "UUID", "signature": "UUID()", "return_type": "VARCHAR", "description": "Generates a UUID v4 string."},
        {"name": "SLEEP", "signature": "SLEEP(seconds)", "return_type": "INT", "description": "Pauses execution for the given number of seconds."},
        {"name": "LAST_QUERY_ID", "signature": "LAST_QUERY_ID()", "return_type": "VARCHAR", "description": "Returns the ID of the last executed query."},
        {"name": "CONNECTION_ID", "signature": "CONNECTION_ID()", "return_type": "INT", "description": "Returns the current connection ID."},
        {"name": "DATABASE", "signature": "DATABASE()", "return_type": "VARCHAR", "description": "Returns the name of the current database."},
        {"name": "VERSION", "signature": "VERSION()", "return_type": "VARCHAR", "description": "Returns the StarRocks server version string."},
        {"name": "USER", "signature": "USER()", "return_type": "VARCHAR", "description": "Returns the current user name."},
    ],
    "Window": [
        {"name": "ROW_NUMBER", "signature": "ROW_NUMBER() OVER (...)", "return_type": "BIGINT", "description": "Assigns a sequential integer to each row within the partition."},
        {"name": "RANK", "signature": "RANK() OVER (...)", "return_type": "BIGINT", "description": "Returns the rank with gaps for ties."},
        {"name": "DENSE_RANK", "signature": "DENSE_RANK() OVER (...)", "return_type": "BIGINT", "description": "Returns the rank without gaps for ties."},
        {"name": "LAG", "signature": "LAG(expr [, offset [, default]]) OVER (...)", "return_type": "Same as expr", "description": "Returns the value of expr from a preceding row."},
        {"name": "LEAD", "signature": "LEAD(expr [, offset [, default]]) OVER (...)", "return_type": "Same as expr", "description": "Returns the value of expr from a following row."},
        {"name": "NTILE", "signature": "NTILE(n) OVER (...)", "return_type": "BIGINT", "description": "Distributes rows into n buckets and returns the bucket number."},
        {"name": "FIRST_VALUE", "signature": "FIRST_VALUE(expr) OVER (...)", "return_type": "Same as expr", "description": "Returns the first value in the window frame."},
        {"name": "LAST_VALUE", "signature": "LAST_VALUE(expr) OVER (...)", "return_type": "Same as expr", "description": "Returns the last value in the window frame."},
    ],
    "TableFunction": [
        {"name": "UNNEST", "signature": "UNNEST(array_column)", "return_type": "Rows", "description": "Expands an array column into multiple rows."},
        {"name": "GENERATE_SERIES", "signature": "GENERATE_SERIES(start, stop [, step])", "return_type": "Rows", "description": "Generates a series of numbers from start to stop."},
        {"name": "FILES", "signature": "FILES('path', format, ...)", "return_type": "Table", "description": "Reads external files (S3, HDFS, local) as a table."},
        {"name": "TVF_QUERY", "signature": "Query external sources as tables", "return_type": "Table", "description": "Various table-valued functions for querying catalogs and external data."},
        {"name": "SHOW", "signature": "SHOW FUNCTIONS / TABLES / ...", "return_type": "Table", "description": "Metadata listing functions that return tabular results."},
    ],
    "AI": [
        {"name": "AI_GENERATE_EMBEDDINGS", "signature": "AI_GENERATE_EMBEDDINGS(model, text)", "return_type": "ARRAY<FLOAT>", "description": "Generates vector embeddings from text using a registered model."},
        {"name": "AI_ANALYZE", "signature": "AI_ANALYZE(model, prompt)", "return_type": "VARCHAR", "description": "Sends a prompt to an AI model and returns the response."},
        {"name": "COSINE_SIMILARITY", "signature": "COSINE_SIMILARITY(vec1, vec2)", "return_type": "FLOAT", "description": "Computes cosine similarity between two vectors."},
        {"name": "L2_DISTANCE", "signature": "L2_DISTANCE(vec1, vec2)", "return_type": "FLOAT", "description": "Computes Euclidean (L2) distance between two vectors."},
        {"name": "APPROX_TOP_K", "signature": "APPROX_TOP_K(column, k)", "return_type": "ARRAY", "description": "Returns approximate top-k values using vector/index-based search."},
    ],
    "Meta": [
        {"name": "CURRENT_DATABASE", "signature": "CURRENT_DATABASE()", "return_type": "VARCHAR", "description": "Returns the name of the current database context."},
        {"name": "CURRENT_ROLE", "signature": "CURRENT_ROLE()", "return_type": "VARCHAR", "description": "Returns the active role for the current session."},
        {"name": "CURRENT_USER", "signature": "CURRENT_USER()", "return_type": "VARCHAR", "description": "Returns the current authenticated user."},
        {"name": "CURRENT_CATALOG", "signature": "CURRENT_CATALOG()", "return_type": "VARCHAR", "description": "Returns the current catalog name."},
        {"name": "SESSION_USER", "signature": "SESSION_USER()", "return_type": "VARCHAR", "description": "Returns the session user (may differ from current_user with SET ROLE)."},
        {"name": "CURRENT_CLUSTER", "signature": "CURRENT_CLUSTER()", "return_type": "VARCHAR", "description": "Returns the current warehouse/cluster name."},
    ],
}


class FunctionService:
    """Service for built-in function reference and UDF management."""

    # ------------------------------------------------------------------
    # User-Defined Functions
    # ------------------------------------------------------------------

    async def list_udfs(self, database: Optional[str] = None) -> list[UDFResponse]:
        """List UDFs via SHOW FULL FUNCTIONS."""
        async with db.system_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SHOW FULL FUNCTIONS")
                rows = await cur.fetchall()
                desc = [d[0] for d in cur.description]

        functions: list[UDFResponse] = []
        for row in rows:
            record = dict(zip(desc, row))
            db_name = str(record.get("Db", ""))
            if database and db_name != database:
                continue
            functions.append(
                UDFResponse(
                    name=str(record.get("Name", "")),
                    database=db_name,
                    function_type=str(record.get("Type", "")),
                    scope="global" if "GLOBAL" in str(record.get("Type", "")).upper() else "database",
                    args=str(record.get("Arguments", "")),
                    return_type=str(record.get("Return_type", "")),
                )
            )
        return functions

    async def create_udf(self, data: UDFCreate) -> str:
        """Build and execute a CREATE FUNCTION statement. Returns the SQL."""
        args_sql = ", ".join(f"{a['name']} {a['type']}" for a in data.args)

        if data.function_type == "sql":
            scope_prefix = "GLOBAL " if data.scope == "global" else ""
            if data.scope == "global":
                qualified = data.name
            else:
                qualified = f"{data.database}.{data.name}" if data.database else data.name
            sql = f"CREATE {scope_prefix}FUNCTION {qualified}({args_sql}) RETURNS {data.return_type} AS {data.body}"

        elif data.function_type == "java":
            qualified = f"{data.database}.{data.name}" if data.database else data.name
            props = data.properties or {}
            props_sql = ", ".join(f'"{k}"="{v}"' for k, v in props.items())
            sql = f"CREATE FUNCTION {qualified}({args_sql}) RETURNS {data.return_type} PROPERTIES ({props_sql})"

        elif data.function_type == "python":
            qualified = f"{data.database}.{data.name}" if data.database else data.name
            props = data.properties or {}
            props_sql = ", ".join(f'"{k}"="{v}"' for k, v in props.items())
            sql = f"CREATE FUNCTION {qualified}({args_sql}) RETURNS {data.return_type} PROPERTIES ({props_sql})"
        else:
            raise ValueError(f"Unsupported function_type: {data.function_type}")

        async with db.system_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql)

        return sql

    async def drop_udf(self, database: str, name: str) -> str:
        """Drop a UDF. Returns the SQL executed."""
        qualified = f"{database}.{name}" if database else name
        sql = f"DROP FUNCTION IF EXISTS {qualified}"

        async with db.system_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql)

        return sql

    def list_built_in(
        self,
        category: Optional[str] = None,
        search: Optional[str] = None,
    ) -> tuple[list[BuiltInFunction], list[FunctionCategory]]:
        """Return filtered built-in functions and category metadata."""
        results: list[BuiltInFunction] = []
        search_lower = search.lower() if search else None

        for cat_name, funcs in BUILTIN_FUNCTIONS.items():
            for f in funcs:
                if category and cat_name.lower() != category.lower():
                    continue
                if search_lower:
                    if search_lower not in f["name"].lower() and search_lower not in f["description"].lower():
                        continue
                results.append(BuiltInFunction(category=cat_name, **f))

        categories = [
            FunctionCategory(name=cat, count=len(funcs))
            for cat, funcs in BUILTIN_FUNCTIONS.items()
        ]

        return results, categories


# Singleton
function_service = FunctionService()
