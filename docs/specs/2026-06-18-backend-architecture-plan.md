# Nova Backend — Deep Research & Architecture Plan

> **Date:** June 18, 2026
> **Status:** Draft — Pending Review
> **Stack:** Python 3.11, FastAPI, uv, asyncmy, StarRocks 4.1.x, MinIO

---

## Executive Summary

Nova backend perlu dibangun dari nol dengan arsitektur **domain-driven modular monolith**. Setiap feature module (auth, query, stages, objects, users, dll.) adalah self-contained unit dengan router, service, schemas, dan dependencies sendiri. Semua test dijalankan melawan **engine asli** (StarRocks, MinIO, Redis) via Docker Compose — zero mocking untuk integration tests.

Key decisions:
- **asyncmy** (bukan pymysql) — async MySQL driver, 20-40% lebih cepat
- **JWT + Redis session** — StarRocks native auth, password encrypted di Redis
- **Command pattern** (à la Superset) — setiap operasi = testable command class
- **uv** — PEP 735 dependency groups, hatchling build backend
- **NO PostgreSQL** — semua state di StarRocks NOVA_SYSTEM (sesuai spec)

---

## 1. Architecture Overview

### 1.1 Layered Modular Monolith

```
┌─────────────────────────────────────────────────┐
│                  API Layer                       │
│         (routers, schemas, deps)                 │
├─────────────────────────────────────────────────┤
│              Service Layer                       │
│      (business logic, command pattern)            │
├─────────────────────────────────────────────────┤
│             Repository Layer                     │
│    (StarRocks SQL, S3/MinIO ops, Redis)          │
├─────────────────────────────────────────────────┤
│            Infrastructure Layer                  │
│   (connections, config, security, exceptions)    │
└─────────────────────────────────────────────────┘
```

### 1.2 Module Structure (per feature)

```
app/modules/<module>/
├── __init__.py
├── router.py          # APIRouter — thin, only validation + response
├── service.py         # Business logic — testable independently
├── commands.py        # Command classes (optional — for complex ops)
├── schemas.py         # Pydantic request/response models
├── repository.py      # Data access — StarRocks SQL, S3 ops
├── deps.py            # Module-specific dependencies
└── exceptions.py      # Module-specific exceptions
```

---

## 2. Project Structure

```
backend/
├── pyproject.toml                 # uv project config
├── uv.lock                        # Lockfile
├── .python-version                # 3.11
├── .env.example                   # Template env vars
├── Dockerfile                     # Production image
├── docker-compose.yml             # Dev: StarRocks + MinIO + Redis
├── docker-compose.test.yml        # Test: isolated engines on different ports
├── alembic/                       # (future: if app metadata needs migration)
│
├── app/
│   ├── __init__.py
│   ├── main.py                    # App factory + lifespan
│   │
│   ├── core/                      # Infrastructure layer
│   │   ├── __init__.py
│   │   ├── config.py              # Pydantic Settings
│   │   ├── security.py            # JWT, password hashing, Fernet encryption
│   │   ├── database.py            # asyncmy connection factory + pool
│   │   ├── redis.py               # Redis session store
│   │   ├── exceptions.py          # Global exception handlers
│   │   └── deps.py                # Shared dependencies (get_current_user, etc.)
│   │
│   ├── modules/                   # Feature modules (domain-driven)
│   │   ├── __init__.py
│   │   │
│   │   ├── auth/                  # Authentication & session management
│   │   │   ├── router.py          # POST /login, /logout, /setup, /refresh
│   │   │   ├── service.py         # verify_credentials, create_session
│   │   │   ├── schemas.py         # LoginRequest, TokenResponse, SetupRequest
│   │   │   ├── repository.py      # Session CRUD in Redis
│   │   │   └── deps.py            # get_current_user, require_admin
│   │   │
│   │   ├── query/                 # SQL execution & dialect translation
│   │   │   ├── router.py          # POST /execute, /explain
│   │   │   ├── service.py         # Query orchestration
│   │   │   ├── schemas.py         # QueryRequest, QueryResponse
│   │   │   ├── repository.py      # Raw SQL execution via asyncmy
│   │   │   └── dialect/           # @stage SQL dialect engine
│   │   │       ├── __init__.py
│   │   │       ├── parser.py      # Parse @stage references
│   │   │       ├── translator.py  # @stage → FILES() translation
│   │   │       ├── injector.py    # Credential injection
│   │   │       └── detector.py    # File format auto-detection
│   │   │
│   │   ├── objects/               # Catalog/DB/Schema/Table/View browser
│   │   │   ├── router.py          # GET /catalogs, /databases, /schemas, /tables, /views
│   │   │   ├── service.py         # Object listing, detail, DDL
│   │   │   ├── schemas.py         # CatalogNode, TableDetail, ColumnInfo
│   │   │   └── repository.py      # SHOW/DESC/INFORMATION_SCHEMA queries
│   │   │
│   │   ├── stages/                # Stage management & file operations
│   │   │   ├── router.py          # CRUD stages + file upload/download
│   │   │   ├── service.py         # Stage lifecycle, file ops
│   │   │   ├── schemas.py         # StageCreate, FileInfo, StageResponse
│   │   │   ├── repository.py      # Stage CRUD in NOVA_SYSTEM.CONFIG
│   │   │   └── storage.py         # StorageProvider abstraction
│   │   │
│   │   ├── users/                 # User & role management
│   │   │   ├── router.py          # CRUD users, roles, grants
│   │   │   ├── service.py         # CREATE USER/ROLE via StarRocks SQL
│   │   │   ├── schemas.py         # UserCreate, RoleInfo, GrantInfo
│   │   │   └── repository.py      # mysql.user queries, SHOW GRANTS
│   │   │
│   │   ├── tables/                # Table DDL operations
│   │   │   ├── router.py          # CREATE/ALTER/DROP table
│   │   │   ├── service.py         # Table lifecycle, partition mgmt
│   │   │   ├── schemas.py         # TableCreate, PartitionInfo
│   │   │   └── repository.py      # DDL execution
│   │   │
│   │   ├── views/                 # View & materialized view management
│   │   │   ├── router.py
│   │   │   ├── service.py
│   │   │   ├── schemas.py
│   │   │   └── repository.py
│   │   │
│   │   ├── functions/             # UDF management
│   │   │   ├── router.py
│   │   │   ├── service.py
│   │   │   ├── schemas.py
│   │   │   └── repository.py
│   │   │
│   │   ├── tasks/                 # Task scheduling (SUBMIT TASK)
│   │   │   ├── router.py
│   │   │   ├── service.py
│   │   │   ├── schemas.py
│   │   │   └── repository.py
│   │   │
│   │   ├── pipes/                 # Continuous ingestion
│   │   │   ├── router.py
│   │   │   ├── service.py
│   │   │   ├── schemas.py
│   │   │   └── repository.py
│   │   │
│   │   ├── external_catalogs/     # Hive, Iceberg, Paimon, JDBC, Hudi, Delta
│   │   │   ├── router.py
│   │   │   ├── service.py
│   │   │   ├── schemas.py
│   │   │   └── repository.py
│   │   │
│   │   ├── cluster/               # Cluster monitoring, nodes, health
│   │   │   ├── router.py
│   │   │   ├── service.py
│   │   │   ├── schemas.py
│   │   │   └── repository.py
│   │   │
│   │   ├── resource_groups/       # CPU isolation, query queues
│   │   │   ├── router.py
│   │   │   ├── service.py
│   │   │   ├── schemas.py
│   │   │   └── repository.py
│   │   │
│   │   ├── ai_ml/                 # AI functions, ML models
│   │   │   ├── router.py
│   │   │   ├── service.py
│   │   │   ├── schemas.py
│   │   │   └── repository.py
│   │   │
│   │   ├── dashboards/            # Charts, widgets, auto-refresh
│   │   │   ├── router.py
│   │   │   ├── service.py
│   │   │   ├── schemas.py
│   │   │   └── repository.py
│   │   │
│   │   ├── backup/                # Snapshots, restore, recycle bin
│   │   │   ├── router.py
│   │   │   ├── service.py
│   │   │   ├── schemas.py
│   │   │   └── repository.py
│   │   │
│   │   ├── governance/            # Masking, row access, tagging, lineage
│   │   │   ├── router.py
│   │   │   ├── service.py
│   │   │   ├── schemas.py
│   │   │   └── repository.py
│   │   │
│   │   ├── variables/             # Session/global vars, password policies
│   │   │   ├── router.py
│   │   │   ├── service.py
│   │   │   ├── schemas.py
│   │   │   └── repository.py
│   │   │
│   │   └── system/                # Compaction, data sharing, storage volumes
│   │       ├── router.py
│   │       ├── service.py
│   │       ├── schemas.py
│   │       └── repository.py
│   │
│   └── common/                    # Shared utilities
│       ├── __init__.py
│       ├── sql_guard.py           # ACCOUNTADMIN protection, dangerous SQL blocker
│       ├── pagination.py          # Cursor-based pagination helper
│       ├── result_formatter.py    # Standardize query results
│       └── nova_system.py         # NOVA_SYSTEM init, schema creation
│
└── tests/
    ├── conftest.py                # Session-scoped Docker fixtures
    ├── unit/                      # Pure logic — no engine needed
    │   ├── test_sql_parser.py
    │   ├── test_sql_translator.py
    │   ├── test_credential_injector.py
    │   ├── test_format_detector.py
    │   ├── test_sql_guard.py
    │   └── test_pagination.py
    │
    └── integration/               # Real engines via Docker Compose
        ├── conftest.py            # Engine-specific fixtures
        ├── test_auth_flow.py      # Login → JWT → session → logout
        ├── test_query_execute.py  # SQL execution, @stage dialect
        ├── test_objects_browse.py # Catalog → DB → Schema → Table tree
        ├── test_stage_crud.py     # Create stage, upload, download, delete
        ├── test_user_management.py # Create user, grant role, list users
        ├── test_nova_system.py    # NOVA_SYSTEM init, config CRUD
        └── test_storage_provider.py # MinIO operations
```

---

## 3. Core Systems Design

### 3.1 Config System (`core/config.py`)

```python
from pydantic_settings import BaseSettings
from pydantic import Field

class Settings(BaseSettings):
    """Type-safe configuration from environment variables."""

    # --- StarRocks ---
    STARROCKS_HOST: str = "localhost"
    STARROCKS_FE_MYSQL_PORT: int = 9030
    STARROCKS_HTTP_PORT: int = 8030
    STARROCKS_ROOT_USER: str = "root"
    STARROCKS_ROOT_PASSWORD: str = ""

    # --- Redis (session store) ---
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- JWT ---
    SECRET_KEY: str = Field(..., description="openssl rand -hex 32")
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60

    # --- Encryption ---
    FERNET_KEY: str = Field(..., description="Fernet key for password encryption")

    # --- Server ---
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = False
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    # --- Storage (from nova.yaml) ---
    NOVA_CONFIG_PATH: str = "nova.yaml"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

settings = Settings()
```

### 3.2 Database Connection Factory (`core/database.py`)

```python
import asyncmy
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from app.core.config import settings

class StarRocksConnectionFactory:
    """
    Creates asyncmy connections to StarRocks.
    
    Two modes:
    - System connections: admin pool for metadata queries
    - User connections: per-request, user's own credentials (RBAC-respecting)
    """

    def __init__(self):
        self._system_pool: asyncmy.Pool | None = None

    async def init_system_pool(self):
        """Create admin pool on startup."""
        self._system_pool = await asyncmy.create_pool(
            host=settings.STARROCKS_HOST,
            port=settings.STARROCKS_FE_MYSQL_PORT,
            user=settings.STARROCKS_ROOT_USER,
            password=settings.STARROCKS_ROOT_PASSWORD,
            minsize=2,
            maxsize=10,
            connect_timeout=10,
            autocommit=True,
        )

    async def close_system_pool(self):
        if self._system_pool:
            self._system_pool.close()
            await self._system_pool.wait_closed()

    @asynccontextmanager
    async def system_conn(self) -> AsyncGenerator[asyncmy.Connection, None]:
        """Admin connection for metadata/system queries."""
        async with self._system_pool.acquire() as conn:
            yield conn

    @asynccontextmanager
    async def user_conn(
        self, username: str, password: str, database: str | None = None
    ) -> AsyncGenerator[asyncmy.Connection, None]:
        """Per-request user connection (no pool, RBAC-respecting)."""
        conn = await asyncmy.connect(
            host=settings.STARROCKS_HOST,
            port=settings.STARROCKS_FE_MYSQL_PORT,
            user=username,
            password=password,
            database=database,
            connect_timeout=10,
            read_timeout=300,
            autocommit=True,
        )
        try:
            yield conn
        finally:
            conn.close()

    async def execute_system(self, sql: str, params: list | None = None) -> dict:
        """Execute SQL as system admin, return standardized result."""
        async with self.system_conn() as conn:
            async with conn.cursor(asyncmy.cursors.DictCursor) as cur:
                await cur.execute(sql, params)
                if cur.description:
                    columns = [desc[0] for desc in cur.description]
                    rows = await cur.fetchall()
                    return {"columns": columns, "rows": rows, "row_count": len(rows)}
                return {"columns": [], "rows": [], "affected": cur.rowcount}


# Singleton — initialized in lifespan
db = StarRocksConnectionFactory()
```

### 3.3 Auth System (`modules/auth/`)

```python
# modules/auth/service.py
import asyncmy
from app.core.config import settings
from app.core.security import create_access_token, encrypt_password, decrypt_password
from app.core.redis import session_store

class AuthService:
    """Authenticate against StarRocks directly. No separate user table."""

    async def verify_credentials(self, username: str, password: str) -> bool:
        """Try a real MySQL connection to verify creds."""
        try:
            conn = await asyncmy.connect(
                host=settings.STARROCKS_HOST,
                port=settings.STARROCKS_FE_MYSQL_PORT,
                user=username,
                password=password,
                connect_timeout=5,
            )
            conn.close()
            return True
        except asyncmy.errors.OperationalError:
            return False

    async def get_user_roles(self, username: str, password: str) -> list[str]:
        """Fetch roles via SHOW GRANTS."""
        conn = await asyncmy.connect(
            host=settings.STARROCKS_HOST,
            port=settings.STARROCKS_FE_MYSQL_PORT,
            user=username,
            password=password,
        )
        try:
            async with conn.cursor() as cur:
                await cur.execute("SHOW GRANTS")
                rows = await cur.fetchall()
                return self._parse_roles(rows)
        finally:
            conn.close()

    async def login(self, username: str, password: str) -> dict:
        """Full login flow: verify → get roles → encrypt password → create session."""
        if not await self.verify_credentials(username, password):
            raise InvalidCredentialsError()

        roles = await self.get_user_roles(username, password)
        enc_password = encrypt_password(password)
        session_id = await session_store.create(
            username=username,
            encrypted_password=enc_password,
            roles=roles,
        )
        token = create_access_token(username=username, session_id=session_id)
        return {"access_token": token, "token_type": "bearer", "user": username, "roles": roles}

    async def get_user_connection(self, session_id: str):
        """Retrieve user's encrypted password from Redis, create DB connection."""
        session = await session_store.get(session_id)
        password = decrypt_password(session["encrypted_password"])
        return await asyncmy.connect(
            host=settings.STARROCKS_HOST,
            port=settings.STARROCKS_FE_MYSQL_PORT,
            user=session["username"],
            password=password,
        )
```

```python
# modules/auth/deps.py
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer
from app.core.security import decode_token
from app.core.redis import session_store

bearer_scheme = HTTPBearer()

async def get_current_user(credentials=Depends(bearer_scheme)):
    """Extract user from JWT → verify session in Redis."""
    payload = decode_token(credentials.credentials)
    session = await session_store.get(payload["sid"])
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    return {
        "username": payload["sub"],
        "session_id": payload["sid"],
        "roles": session["roles"],
    }

def require_role(*roles: str):
    """Dependency factory: require specific role(s)."""
    async def _check(user=Depends(get_current_user)):
        if not any(r in user["roles"] for r in roles):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
    return _check
```

### 3.4 Session Store (`core/redis.py`)

```python
import redis.asyncio as aioredis
import uuid
from app.core.config import settings

class SessionStore:
    """Redis-backed session store. Stores encrypted DB passwords."""

    PREFIX = "nova:session:"
    TTL = 3600  # 1 hour

    def __init__(self):
        self._redis: aioredis.Redis | None = None

    async def init(self):
        self._redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)

    async def close(self):
        if self._redis:
            await self._redis.close()

    async def create(self, username: str, encrypted_password: str, roles: list[str]) -> str:
        session_id = str(uuid.uuid4())
        data = {
            "username": username,
            "encrypted_password": encrypted_password,
            "roles": ",".join(roles),
        }
        await self._redis.hset(f"{self.PREFIX}{session_id}", mapping=data)
        await self._redis.expire(f"{self.PREFIX}{session_id}", self.TTL)
        return session_id

    async def get(self, session_id: str) -> dict | None:
        data = await self._redis.hgetall(f"{self.PREFIX}{session_id}")
        if not data:
            return None
        data["roles"] = data["roles"].split(",") if data["roles"] else []
        return data

    async def delete(self, session_id: str):
        await self._redis.delete(f"{self.PREFIX}{session_id}")

    async def refresh(self, session_id: str):
        await self._redis.expire(f"{self.PREFIX}{session_id}", self.TTL)

session_store = SessionStore()
```

### 3.5 SQL Guard (`common/sql_guard.py`)

```python
import re
from app.modules.auth.exceptions import ForbiddenSQLError

BLOCKED_PATTERNS = [
    (r"DROP\s+ROLE\s+ACCOUNTADMIN", "ACCOUNTADMIN role cannot be dropped"),
    (r"REVOKE\s+.*\s+FROM\s+ROLE\s+ACCOUNTADMIN", "Cannot revoke from ACCOUNTADMIN"),
    (r"ALTER\s+ROLE\s+ACCOUNTADMIN", "ACCOUNTADMIN role cannot be altered"),
    (r"DROP\s+USER\s+.*root", "root user cannot be dropped"),
]

def guard_sql(sql: str) -> None:
    """Block dangerous operations on system objects. Raises ForbiddenSQLError."""
    upper = sql.strip().upper()
    for pattern, message in BLOCKED_PATTERNS:
        if re.search(pattern, upper, re.IGNORECASE):
            raise ForbiddenSQLError(message)
```

### 3.6 Storage Provider (`modules/stages/storage.py`)

```python
from abc import ABC, abstractmethod
import boto3
from typing import BinaryIO

class StorageProvider(ABC):
    """Abstract storage backend. S3/MinIO/Azure/GCS implementations."""

    @abstractmethod
    async def list_objects(self, prefix: str) -> list[dict]: ...

    @abstractmethod
    async def get_object(self, key: str) -> bytes: ...

    @abstractmethod
    async def put_object(self, key: str, data: BinaryIO) -> None: ...

    @abstractmethod
    async def delete_object(self, key: str) -> None: ...

    @abstractmethod
    async def get_presigned_url(self, key: str, expires: int = 3600) -> str: ...


class S3StorageProvider(StorageProvider):
    """S3-compatible storage (AWS S3, MinIO, etc.)."""

    def __init__(self, endpoint: str, access_key: str, secret_key: str, bucket: str):
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )
        self._bucket = bucket

    async def list_objects(self, prefix: str) -> list[dict]:
        resp = self._client.list_objects_v2(Bucket=self._bucket, Prefix=prefix, Delimiter="/")
        entries = []
        for p in resp.get("CommonPrefixes", []):
            entries.append({"name": p["Prefix"], "type": "folder"})
        for obj in resp.get("Contents", []):
            entries.append({"name": obj["Key"], "type": "file", "size": obj["Size"], "modified": obj["LastModified"].isoformat()})
        return entries
```

### 3.7 App Factory (`main.py`)

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.database import db
from app.core.redis import session_store
from app.core.exceptions import register_exception_handlers
from app.common.nova_system import init_nova_system

# Import all module routers
from app.modules.auth.router import router as auth_router
from app.modules.query.router import router as query_router
from app.modules.objects.router import router as objects_router
from app.modules.stages.router import router as stages_router
from app.modules.users.router import router as users_router
from app.modules.tables.router import router as tables_router
from app.modules.views.router import router as views_router
from app.modules.functions.router import router as functions_router
from app.modules.tasks.router import router as tasks_router
from app.modules.pipes.router import router as pipes_router
from app.modules.external_catalogs.router import router as ext_router
from app.modules.cluster.router import router as cluster_router
from app.modules.resource_groups.router import router as rg_router
from app.modules.ai_ml.router import router as ai_router
from app.modules.dashboards.router import router as dash_router
from app.modules.backup.router import router as backup_router
from app.modules.governance.router import router as gov_router
from app.modules.variables.router import router as var_router
from app.modules.system.router import router as sys_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    # Startup
    await db.init_system_pool()
    await session_store.init()
    await init_nova_system()
    yield
    # Shutdown
    await db.close_system_pool()
    await session_store.close()


app = FastAPI(
    title="Nova",
    version="0.1.0",
    description="Management console backend",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Exception handlers
register_exception_handlers(app)

# API v1 routers
prefix = "/api/v1"
app.include_router(auth_router,     prefix=f"{prefix}/auth",      tags=["auth"])
app.include_router(query_router,    prefix=f"{prefix}/query",     tags=["query"])
app.include_router(objects_router,  prefix=f"{prefix}/objects",   tags=["objects"])
app.include_router(stages_router,   prefix=f"{prefix}/stages",    tags=["stages"])
app.include_router(users_router,    prefix=f"{prefix}/users",     tags=["users"])
app.include_router(tables_router,   prefix=f"{prefix}/tables",    tags=["tables"])
app.include_router(views_router,    prefix=f"{prefix}/views",     tags=["views"])
app.include_router(functions_router,prefix=f"{prefix}/functions", tags=["functions"])
app.include_router(tasks_router,    prefix=f"{prefix}/tasks",     tags=["tasks"])
app.include_router(pipes_router,    prefix=f"{prefix}/pipes",     tags=["pipes"])
app.include_router(ext_router,      prefix=f"{prefix}/catalogs",  tags=["catalogs"])
app.include_router(cluster_router,  prefix=f"{prefix}/cluster",   tags=["cluster"])
app.include_router(rg_router,       prefix=f"{prefix}/resource-groups", tags=["resource-groups"])
app.include_router(ai_router,       prefix=f"{prefix}/ai",        tags=["ai"])
app.include_router(dash_router,     prefix=f"{prefix}/dashboards", tags=["dashboards"])
app.include_router(backup_router,   prefix=f"{prefix}/backup",    tags=["backup"])
app.include_router(gov_router,      prefix=f"{prefix}/governance", tags=["governance"])
app.include_router(var_router,      prefix=f"{prefix}/variables", tags=["variables"])
app.include_router(sys_router,      prefix=f"{prefix}/system",    tags=["system"])


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}
```

---

## 4. Testing Strategy

### 4.1 Principle: Real Engines, Zero Mocks for Integration Tests

```
Unit tests     → Pure logic, no engine needed (parser, translator, guard)
Integration    → Real StarRocks + MinIO + Redis via Docker Compose
E2E            → Full flow: login → query → result (future)
```

### 4.2 Docker Compose for Tests (`docker-compose.test.yml`)

```yaml
services:
  starrocks-fe:
    image: starrocks/fe-ubuntu:4.1.1
    ports:
      - "29030:9030"    # MySQL protocol — different port to avoid conflict with dev
      - "28030:8030"    # HTTP
    environment:
      - FE_QUERY_PORT=9030
    healthcheck:
      test: ["CMD", "mysql", "-h", "127.0.0.1", "-P", "9030", "-u", "root", "-e", "SELECT 1"]
      interval: 5s
      timeout: 3s
      retries: 30

  starrocks-be:
    image: starrocks/be-ubuntu:4.1.1
    depends_on:
      starrocks-fe:
        condition: service_healthy
    environment:
      - FE_ADDRESS=starrocks-fe:9010

  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    ports:
      - "29000:9000"
      - "29001:9001"
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 5s
      timeout: 3s
      retries: 10

  redis:
    image: redis:7-alpine
    ports:
      - "26379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 2s
      timeout: 2s
      retries: 10
```

### 4.3 Test Fixtures (`tests/conftest.py`)

```python
import subprocess
import asyncio
import pytest
import asyncmy
import boto3
import redis.asyncio as aioredis

# --- Session-scoped: spin up once, shared across all tests ---

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(scope="session")
def docker_services():
    """Start test engines, wait for health, tear down after all tests."""
    subprocess.run(
        ["docker-compose", "-f", "docker-compose.test.yml", "up", "-d", "--wait"],
        check=True,
    )
    yield
    subprocess.run(
        ["docker-compose", "-f", "docker-compose.test.yml", "down", "-v"],
        check=True,
    )

@pytest.fixture(scope="session")
async def sr_root(docker_services):
    """Root connection to StarRocks. Creates test user + roles."""
    for i in range(60):
        try:
            conn = await asyncmy.connect(host="127.0.0.1", port=29030, user="root", password="")
            async with conn.cursor() as cur:
                # Create test user
                await cur.execute("CREATE USER IF NOT EXISTS 'testuser' IDENTIFIED BY 'testpass'")
                await cur.execute("CREATE ROLE IF NOT EXISTS 'test_analyst'")
                await cur.execute("GRANT 'test_analyst' TO 'testuser'")
                await cur.execute("GRANT SELECT ON *.* TO ROLE 'test_analyst'")
            yield conn
            await conn.close()
            return
        except Exception:
            if i == 59:
                raise
            await asyncio.sleep(1)

@pytest.fixture(scope="session")
def minio_client(docker_services):
    """MinIO S3 client with test bucket pre-created."""
    import time; time.sleep(5)
    client = boto3.client(
        "s3",
        endpoint_url="http://127.0.0.1:29000",
        aws_access_key_id="minioadmin",
        aws_secret_access_key="minioadmin",
    )
    client.create_bucket(Bucket="test-stage")
    return client

@pytest.fixture(scope="session")
async def redis_client(docker_services):
    """Async Redis client for session store tests."""
    client = aioredis.from_url("redis://127.0.0.1:26379", decode_responses=True)
    yield client
    await client.close()

@pytest.fixture(scope="function")
async def app_client(sr_root, minio_client, redis_client):
    """FastAPI test client with real engine connections."""
    from httpx import AsyncClient, ASGITransport
    import app.core.config as cfg

    # Override settings for test environment
    cfg.settings.STARROCKS_HOST = "127.0.0.1"
    cfg.settings.STARROCKS_FE_MYSQL_PORT = 29030
    cfg.settings.REDIS_URL = "redis://127.0.0.1:26379"

    from app.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
```

### 4.4 Example Integration Test

```python
# tests/integration/test_auth_flow.py

async def test_login_success(app_client):
    """Real login against StarRocks → JWT returned."""
    resp = await app_client.post("/api/v1/auth/login", json={
        "username": "testuser",
        "password": "testpass",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["user"] == "testuser"
    assert "test_analyst" in data["roles"]

async def test_login_invalid_credentials(app_client):
    """Wrong password → 401."""
    resp = await app_client.post("/api/v1/auth/login", json={
        "username": "testuser",
        "password": "wrongpass",
    })
    assert resp.status_code == 401

async def test_protected_endpoint_requires_auth(app_client):
    """No token → 401."""
    resp = await app_client.get("/api/v1/objects/catalogs")
    assert resp.status_code == 401

async def test_query_with_auth(app_client):
    """Login → execute query → get results."""
    # Login
    login = await app_client.post("/api/v1/auth/login", json={
        "username": "testuser", "password": "testpass"
    })
    token = login.json()["access_token"]

    # Execute query
    resp = await app_client.post("/api/v1/query/execute", json={
        "sql": "SELECT 1 AS test_col"
    }, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["columns"] == ["test_col"]
    assert data["rows"] == [[1]]
```

---

## 5. pyproject.toml

```toml
[project]
name = "nova"
version = "0.1.0"
description = "Nova — management console backend"
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
    # --- Web framework ---
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.34.0",
    # --- Database ---
    "asyncmy>=0.3.0",
    # --- Auth & Security ---
    "python-jose[cryptography]>=3.3.0",
    "cryptography>=44.0.0",       # Fernet encryption
    # --- Storage ---
    "boto3>=1.36.0",
    # --- Session ---
    "redis[hiredis]>=5.2.0",
    # --- Config ---
    "pydantic>=2.10.0",
    "pydantic-settings>=2.7.0",
    # --- HTTP client ---
    "httpx>=0.28.0",
    # --- YAML ---
    "pyyaml>=6.0.0",
]

[dependency-groups]
dev = [
    "ruff>=0.9.0",
    "mypy>=1.14.0",
    "pre-commit>=4.0.0",
    { include-group = "test" },
]
test = [
    "pytest>=8.3.0",
    "pytest-asyncio>=0.25.0",
    "pytest-cov>=6.0.0",
    "httpx>=0.28.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.uv]
default-groups = ["dev"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
target-version = "py311"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]
```

---

## 6. Implementation Phases

### Phase 1: Foundation (Core + Auth)
| Task | Description |
|------|-------------|
| 1.1 | `uv init` + pyproject.toml + dependency install |
| 1.2 | `core/config.py` — Pydantic Settings |
| 1.3 | `core/database.py` — StarRocksConnectionFactory |
| 1.4 | `core/redis.py` — SessionStore |
| 1.5 | `core/security.py` — JWT + Fernet encryption |
| 1.6 | `core/exceptions.py` — Global error handlers |
| 1.7 | `core/deps.py` — get_current_user, require_role |
| 1.8 | `common/nova_system.py` — NOVA_SYSTEM init |
| 1.9 | `common/sql_guard.py` — ACCOUNTADMIN protection |
| 1.10 | `modules/auth/` — login, logout, setup, refresh |
| 1.11 | `main.py` — App factory + lifespan |
| 1.12 | `docker-compose.yml` — Dev environment |
| 1.13 | `docker-compose.test.yml` — Test environment |
| 1.14 | Integration tests: auth flow |

### Phase 2: Query Engine
| Task | Description |
|------|-------------|
| 2.1 | `modules/query/repository.py` — Raw SQL execution |
| 2.2 | `modules/query/dialect/parser.py` — @stage parser |
| 2.3 | `modules/query/dialect/translator.py` — @stage → FILES() |
| 2.4 | `modules/query/dialect/injector.py` — Credential injection |
| 2.5 | `modules/query/dialect/detector.py` — Format auto-detect |
| 2.6 | `modules/query/service.py` — Query orchestration |
| 2.7 | `modules/query/router.py` — /execute, /explain |
| 2.8 | Integration tests: SQL execution + @stage dialect |

### Phase 3: Object Browser
| Task | Description |
|------|-------------|
| 3.1 | `modules/objects/repository.py` — SHOW/DESC/INFO_SCHEMA |
| 3.2 | `modules/objects/service.py` — Tree navigation |
| 3.3 | `modules/objects/router.py` — /catalogs, /databases, /schemas, /tables |
| 3.4 | `modules/tables/` — Table DDL (CREATE/ALTER/DROP) |
| 3.5 | `modules/views/` — View + MV management |
| 3.6 | Integration tests: object browsing + DDL |

### Phase 4: Stage & Storage
| Task | Description |
|------|-------------|
| 4.1 | `modules/stages/storage.py` — StorageProvider abstraction |
| 4.2 | `modules/stages/repository.py` — Stage CRUD in NOVA_SYSTEM |
| 4.3 | `modules/stages/service.py` — File operations |
| 4.4 | `modules/stages/router.py` — CRUD + upload/download |
| 4.5 | Integration tests: stage lifecycle + MinIO operations |

### Phase 5: Administration
| Task | Description |
|------|-------------|
| 5.1 | `modules/users/` — User/role management |
| 5.2 | `modules/resource_groups/` — Resource group management |
| 5.3 | `modules/cluster/` — Cluster monitoring |
| 5.4 | `modules/functions/` — UDF management |
| 5.5 | `modules/tasks/` — Task scheduling |
| 5.6 | `modules/pipes/` — Continuous ingestion |

### Phase 6: Advanced Features
| Task | Description |
|------|-------------|
| 6.1 | `modules/external_catalogs/` — Hive/Iceberg/Paimon |
| 6.2 | `modules/ai_ml/` — AI functions + ML models |
| 6.3 | `modules/dashboards/` — Charts + widgets |
| 6.4 | `modules/backup/` — Snapshots + restore |
| 6.5 | `modules/governance/` — Masking + lineage |
| 6.6 | `modules/variables/` — Session/global vars |
| 6.7 | `modules/system/` — Compaction + sharing + volumes |

---

## 7. Key Design Decisions (with Rationale)

| Decision | Rationale |
|----------|-----------|
| **asyncmy, not pymysql** | Async native, 20-40% faster, works with FastAPI event loop |
| **Per-request user connections** | Respects StarRocks RBAC natively — each query runs as the user |
| **System pool for metadata** | Admin connection for SHOW TABLES, INFORMATION_SCHEMA — faster than per-request |
| **JWT + Redis, not DB sessions** | Stateless API, fast session lookup, auto-expiry via Redis TTL |
| **Fernet for password encryption** | Symmetric, fast, store encrypted DB password in Redis session |
| **Domain modules, not flat services** | Each module is self-contained, testable, can be extracted to microservice later |
| **Command pattern for complex ops** | Testable business logic independent of HTTP layer (Superset-proven) |
| **NO PostgreSQL** | All state in StarRocks NOVA_SYSTEM — consistent with spec, fewer deps |
| **uv + hatchling** | Fast dependency resolution, PEP 735 groups, modern Python toolchain |
| **Docker Compose for tests** | Real engines = real behavior, no mock drift, CI-ready |

---

## 8. Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| StarRocks asyncmy compatibility | asyncmy is MySQL-protocol compatible; StarRocks uses MySQL protocol. Pin version. |
| Long-running queries block event loop | Set `read_timeout=300`, use `asyncio.wait_for` for timeout control |
| Redis session loss | JWT contains `sub` + `sid`; if Redis lost, user re-authenticates (acceptable) |
| Docker Compose test speed | Session-scoped fixtures — engines start once per test suite, not per test |
| NOVA_SYSTEM init race condition | `CREATE IF NOT EXISTS` is idempotent; init runs once in lifespan |
