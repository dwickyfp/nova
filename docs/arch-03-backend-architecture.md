# Architecture 03: Backend Architecture

> FastAPI application structure, API design, and data flow.

---

## Project Structure

```
backend/
├── pyproject.toml
├── .env.example
├── app/
│   ├── __init__.py
│   ├── main.py                    # FastAPI app factory
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py              # pydantic-settings
│   │   ├── security.py            # credential encryption
│   │   └── dependencies.py        # FastAPI dependencies
│   │
│   ├── db/
│   │   ├── __init__.py
| `starrocks.py           | StarRocks MySQL connector |
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── storage_connection.py  # StorageConnection ORM
│   │   └── stage.py               # Stage ORM
│   │
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── sql.py                 # SQLRequest, SQLResponse
│   │   ├── stage.py               # Stage schemas
│   │   ├── storage.py             # StorageConnection schemas
│   │   └── catalog.py             # Catalog schemas
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── stage_service.py       # Stage business logic
│   │   ├── catalog_service.py     # Catalog operations
│   │   ├── table_service.py       # Table DDL operations
│   │   ├── view_service.py        # View operations
│   │   ├── function_service.py    # Function operations
│   │   ├── task_service.py        # Task operations
│   │   ├── pipe_service.py        # Pipe operations
│   │   ├── user_service.py        # User/role management
│   │   ├── resource_service.py    # Resource group management
│   │   └── cluster_service.py     # Cluster monitoring
│   │
│   ├── sql_dialect/
│   │   ├── __init__.py
│   │   ├── pipeline.py            # SQLPipeline orchestrator
│   │   ├── parser.py              # DialectParser
│   │   ├── translator.py          # DialectTranslator
│   │   ├── credential_injector.py # SQLCredentialInjector
│   │   ├── format_detector.py     # Auto-detect file formats
│   │   └── commands/
│   │       ├── __init__.py
│   │       ├── stage_query.py
│   │       ├── stage_browse.py
│   │       ├── stage_load.py
│   │       └── stage_export.py
│   │
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── provider.py            # StorageProvider ABC
│   │   ├── factory.py             # StorageFactory
│   │   ├── s3_provider.py         # S3Provider
│   │   ├── azure_provider.py      # AzureBlobProvider
│   │   └── gcs_provider.py        # GCSProvider
│   │
│   └── api/
│       └── v1/
│           ├── __init__.py
│           ├── router.py          # API router aggregator
│           └── endpoints/
│               ├── sql.py         # POST /execute, /explain
│               ├── catalogs.py    # CRUD catalogs
│               ├── databases.py   # CRUD databases
│               ├── tables.py      # CRUD tables
│               ├── views.py       # CRUD views
│               ├── functions.py   # CRUD functions
│               ├── tasks.py       # CRUD tasks
│               ├── pipes.py       # CRUD pipes
│               ├── stages.py      # CRUD stages + file ops
│               ├── storage.py     # CRUD storage connections
│               ├── users.py       # CRUD users/roles
│               ├── resources.py   # CRUD resource groups
│               └── cluster.py     # Cluster status
│
└── tests/
    ├── conftest.py
    ├── test_sql_dialect.py
    ├── test_storage_providers.py
    └── test_stage_service.py
    └── test_nova_system.py
```

---

## API Endpoints

### SQL Execution

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/sql/execute` | Execute SQL (with dialect rewrite) |
| POST | `/api/v1/sql/explain` | EXPLAIN query |

### Catalogs

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/catalogs` | List all catalogs |
| POST | `/api/v1/catalogs` | Create external catalog |
| GET | `/api/v1/catalogs/{name}` | Catalog details |
| DELETE | `/api/v1/catalogs/{name}` | Drop catalog |

### Databases

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/databases` | List databases |
| POST | `/api/v1/databases` | Create database |
| GET | `/api/v1/databases/{name}` | Database details |
| DELETE | `/api/v1/databases/{name}` | Drop database |

### Tables

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/tables?db={}` | List tables |
| POST | `/api/v1/tables` | Create table |
| GET | `/api/v1/tables/{name}` | Table details (columns, partitions, indexes) |
| DELETE | `/api/v1/tables/{name}` | Drop table |
| GET | `/api/v1/tables/{name}/ddl` | SHOW CREATE TABLE |
| GET | `/api/v1/tables/{name}/preview?limit=100` | Preview data |

### Stages

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/stages?db={}&schema={}` | List stages |
| POST | `/api/v1/stages` | Create stage |
| DELETE | `/api/v1/stages/{id}` | Drop stage |
| GET | `/api/v1/stages/{id}/files?prefix={}` | List files |
| POST | `/api/v1/stages/{id}/upload` | Upload file |
| GET | `/api/v1/stages/{id}/download?path={}` | Download file |
| DELETE | `/api/v1/stages/{id}/file?path={}` | Delete file |

### Storage Connections

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/connections` | List connections (read-only from nova.yaml) |
| POST | `/api/v1/connections/{name}/test` | Test connection |
| POST | `/api/v1/connections/{id}/test` | Test connection |

---

## Request/Response Examples

### Execute SQL

```json
// POST /api/v1/sql/execute
{
    "sql": "SELECT * FROM @stage1.data.csv LIMIT 10",
    "database": "DATALAKE",
    "schema": "bronze"
}

// Response
{
    "success": true,
    "columns": ["id", "name", "amount"],
    "rows": [[1, "Andi", 150000], [2, "Budi", 230000]],
    "row_count": 2,
    "original_sql": "SELECT * FROM @stage1.data.csv LIMIT 10",
    "executed_sql": "SELECT * FROM FILES('path'='s3://...', ... ) LIMIT 10",
    "warnings": ["✅ @stage1.data.csv"]
}
```

### List Stage Files

```json
// GET /api/v1/stages/abc-123/files?prefix=data/

{
    "stage": "stage1",
    "path": "stage1.data",
    "entries": [
        {"name": "trx.csv", "type": "file", "size": "450 KB", "modified": "2026-06-18T10:00:00", "query_ref": "@stage1.data.trx.csv"},
        {"name": "archive", "type": "folder", "size": null, "modified": null, "query_ref": "@stage1.data.archive"}
    ]
}
```

---

## Data Flow: SQL Execute

```
1. POST /api/v1/sql/execute
   │
2. FastAPI validates request (Pydantic)
   │
3. SQLPipeline.execute(sql, context)
   │
   ├─ 3a. DialectParser.parse(sql)
   │       → CommandType.STAGE_QUERY
   │
   ├─ 3b. DialectTranslator.translate(parsed)
   │       → SELECT * FROM FILES('path'='...stage1/data.csv', 'format'='csv')
   │
   ├─ 3c. SQLCredentialInjector.inject(sql)
   │       → FILES(..., 'aws.s3.access_key'='...')
   │
   └─ 3d. StarRocks.execute(final_sql)
           → {columns, rows}
   │
4. Return SQLResponse
```

---

## StarRocks Connection Management

```python
# db/starrocks.py

class StarRocksPool:
    """Connection pool for StarRocks (MySQL protocol)."""
    
    def __init__(self, host, port, user, password):
        self.pool = []
        self.config = {"host": host, "port": port, "user": user, "password": password}
    
    @contextmanager
    def connection(self, database: str = None):
        conn = pymysql.connect(
            **self.config,
            database=database,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )
        try:
            yield conn
        finally:
            conn.close()
    
    def execute(self, sql: str, database: str = None) -> dict:
        with self.connection(database) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                if cur.description:
                    columns = [desc[0] for desc in cur.description]
                    rows = [list(r.values()) for r in cur.fetchall()]
                    return {"columns": columns, "rows": rows}
                return {"columns": [], "rows": [], "affected": cur.rowcount}
```
