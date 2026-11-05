# StarRocks Ranger active-role patch

Nova pins StarRocks `4.1.4` at commit
`4a9848edf03f5c936dac664b2d52527f48e72eb0`, matching Nova's existing
engine baseline and L3 regression suite. The patch is
opt-in upstream-compatible behavior; Nova enables it with:

```xml
<name>ranger.plugin.starrocks.role.context.mode</name>
<value>active_role</value>
```

In this mode every object, row-filter, and masking request contains exactly the
single role active in `ConnectContext`. Zero or multiple active roles fail
closed. Root remains an explicit StarRocks bootstrap exception and is blocked
from Nova's public query surfaces.

Verify against a clean checkout:

```bash
./patches/starrocks/verify.sh
```

The integration image in `docker/ranger/starrocks-fe.Dockerfile` applies the
Ranger patches and the task active-role patch, replacing the compiled classes in the
official runtime `fe-core-4.1.4.jar`. It also pins Nashorn 15.4 and ASM Commons
9.4 (with SHA-256 verification), because Ranger 2.8's dynamic user-attribute
expressions need a JSR-223 JavaScript engine that Java 17 no longer bundles.
The build does not vendor the StarRocks source tree.

## Task execution role

`4.1.4-task-active-role.patch` fixes upstream `TaskRun.switchUser`, which normally
activates every role assigned to the creator. The async builder now captures the submitting context. A caller-submitted task
snapshots that caller's selected role set and intersects it with current assignments, so a
revoked role cannot be resurrected. The user identity must match the submitting
context. Internal runs without a matching caller keep upstream behavior; Nova
uses one-shot submissions for its scheduled graphs under the bound service role.

This is an engine build patch, not a Java application component. The Python
worker still submits SQL through the authenticated user connection. Live
multi-role acceptance results are in
`docs/benchmarks/task-role-ownership-2026-09-26/`.

## Snowflake JDBC catalog

`4.1.4-jdbc-snowflake.patch` lets a StarRocks JDBC catalog read Snowflake. Upstream
`JDBCMetadata` picks a schema resolver from the driver class name and rejects every driver
outside MySQL, PostgreSQL, MariaDB, ClickHouse, Oracle and SQL Server. The patch adds:

- `SnowflakeSchemaResolver`. One catalog is the Snowflake database selected by `db=` in the
  JDBC URI, and StarRocks databases are its schemas. `INFORMATION_SCHEMA` is hidden. Metadata
  patterns escape `_` and `%`, so `SALES_EU` never matches `SALESXEU`. Names are used exactly
  as Snowflake stores them, upper case for unquoted identifiers. `sf.sales.orders` therefore
  does not resolve to `SALES.ORDERS`, so one table never has two spellings for Ranger policies
  to miss.
- Type mapping: scale-0 `NUMBER`, including `INT`/`BIGINT`, maps to `BIGINT`. That matches the
  driver default `JDBC_TREAT_DECIMAL_AS_INT=true`, which reads these columns as `Long`. With
  `JDBC_TREAT_DECIMAL_AS_INT=false` in the URI they map to `DECIMAL(p,0)`.
  - `NUMBER(p,s)` maps to `DECIMAL(p,s)`, and `FLOAT` to `DOUBLE`.
  - `VARCHAR`/`TEXT` map to `VARCHAR(n)`, and `BOOLEAN`, `DATE` and `TIME` map directly.
  - `TIMESTAMP_NTZ/LTZ/TZ` map to `DATETIME`.
  - `VARIANT`/`OBJECT`/`ARRAY` map to `JSON`.
  - Structured `OBJECT(...)`/`ARRAY(...)`, `GEOGRAPHY` and `GEOMETRY` map to `VARCHAR`.
  - `BINARY` maps to `VARBINARY`.
- `JDBCScanNode` double-quotes columns, and `JDBC_TABLENAME` is `"SCHEMA"."TABLE"`, for
  `jdbc:snowflake` URIs.
- Isolated driver loading. The 85 MB Snowflake driver bundles an unshaded `zstd-jni` that would
  shadow the FE's own copy, so it is not on the FE classpath. When a Snowflake driver class is
  missing from the classpath, `JDBCMetadata` loads it in its own `URLClassLoader` from
  `driver_url`. That URL must be a local `file://` JAR whose MD5 matches the catalog's recorded
  `checksum`, which is the same check the BE makes. The FE never loads remote driver code. Other
  drivers keep upstream behavior.

The FE and BE images both ship `snowflake-jdbc` 3.28.0 at
`/opt/starrocks/drivers/snowflake-jdbc-3.28.0.jar`, pinned by SHA-256 in
`docker/ranger/starrocks-fe.Dockerfile` and `docker/starrocks-be.Dockerfile`; keep both pins
equal. The BE JDBC scanner fetches the driver from the same `file://` URL (verified on the
pinned BE), so creating or querying a catalog downloads nothing.

The FE image build compiles the patched classes and runs `SnowflakeSchemaResolverTest`
(JUnit console 1.11.4, SHA-256 pinned) against the runtime FE libraries. A failing test fails
the build.

Catalog URIs need these parameters:

- `db=<DATABASE>`, which is required. Add `warehouse=` and `role=` as needed.
- `JDBC_QUERY_RESULT_FORMAT=JSON`. The BE's embedded JVM has no
  `--add-opens java.base/java.nio`, which the driver's default Arrow format needs on Java 17.
- `TIMEZONE=<the engine time_zone>`. Without it, `TIMESTAMP_LTZ` is returned in the Snowflake
  session time zone (`America/Los_Angeles` by default). `TIMESTAMP_TZ` keeps its stored
  wall-clock time and drops the offset.
- No secrets. `SHOW CREATE CATALOG` omits `password`, but it prints `jdbc_uri` in full. Both
  supported credentials stay out of the URI:
  - A Programmatic Access Token goes in the `password` property, with no `authenticator`.
    Rotate it with `ALTER CATALOG ... SET ("password" = ...)`, which takes effect on the next
    query.
  - Key pair: `authenticator=SNOWFLAKE_JWT&private_key_file=<path>`, with the key file present
    on the FE and every BE.

  Both were verified live.

Pushdown matches other JDBC catalogs: projections, simple filters and `LIMIT` are pushed, while
aggregations and joins run in StarRocks. Live acceptance results are in
`docs/benchmarks/snowflake-jdbc-2026-11-05/`.
