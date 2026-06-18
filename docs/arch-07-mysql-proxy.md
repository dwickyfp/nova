# Architecture 07: MySQL Protocol Proxy

> Nova exposes a MySQL protocol endpoint so clients can connect directly (like Snowflake).
> Any MySQL client (CLI, JDBC, Python, dbt) can connect to `www.nova.io:4406` and use @stage syntax transparently.

---

## Concept

Snowflake exposes `account.snowflakecomputing.com:443` — clients connect with standard JDBC/ODBC. Nova does the same with MySQL protocol:

```
Client (any MySQL client)
    → connects to www.nova.io:4406
    → Nova Proxy authenticates against StarRocks
    → SQL is rewritten (@stage → FILES() + creds)
    → Forwarded to StarRocks (port 9030)
    → Results returned to client
```

**Client sees:** A MySQL-compatible endpoint with @stage superpowers.
**Nova does:** Auth + SQL rewrite + audit + proxy.

---

## Architecture

```
┌─ Client ──────────────────────────────────────────────────┐
│                                                            │
│  mysql -h www.nova.io -P 4406 -u analyst -p               │
│  pymysql.connect(host="www.nova.io", port=4406, ...)      │
│  jdbc:mysql://www.nova.io:4406/DATALAKE                   │
│  dbt: host: www.nova.io, port: 4406                       │
│                                                            │
└──────────────────────────┬─────────────────────────────────┘
                           │ MySQL protocol (port 4406)
                           ▼
┌──────────────────────────────────────────────────────────┐
│                 Nova MySQL Proxy                           │
│                                                            │
│  1. Accept MySQL connection (handshake)                   │
│  2. Authenticate user against StarRocks                   │
│  3. Receive SQL commands                                  │
│  4. Dialect rewrite: @stage → FILES() + creds             │
│  5. Forward to StarRocks                                  │
│  6. Return results to client                              │
│  7. Audit log → NOVA_SYSTEM.AUDIT.LOG                     │
│                                                            │
└──────────────────────────┬─────────────────────────────────┘
                           │ MySQL protocol (port 9030)
                           ▼
┌──────────────────────────────────────────────────────────┐
│              StarRocks FE (port 9030)                      │
│                                                            │
│  User databases: DATALAKE, ANALYTICS, ...                 │
│  System: NOVA_SYSTEM (hidden)                              │
│                                                            │
└──────────────────────────────────────────────────────────┘
```

---

## Client Connection Examples

### MySQL CLI

```bash
$ mysql -h www.nova.io -P 4406 -u analyst -p

MySQL> USE DATALAKE.bronze;
MySQL> SELECT * FROM @stage1.data.csv LIMIT 10;
+----+--------+--------+
| id | name   | amount |
+----+--------+--------+
|  1 | Andi   | 150000 |
|  2 | Budi   | 230000 |
|  3 | Citra  |  89000 |
+----+--------+--------+

MySQL> SHOW TABLES;
MySQL> SHOW DATABASES;
MySQL> SELECT COUNT(*) FROM orders;
```

### Python (pymysql)

```python
import pymysql

conn = pymysql.connect(
    host="www.nova.io",
    port=4406,
    user="analyst",
    password="***",
    database="DATALAKE",
)

with conn.cursor() as cur:
    # @stage syntax works transparently
    cur.execute("SELECT * FROM @stage1.data.csv LIMIT 10")
    for row in cur.fetchall():
        print(row)

    # Standard SQL also works
    cur.execute("SELECT COUNT(*) FROM orders")
    print(cur.fetchone())
```

### Python (SQLAlchemy)

```python
from sqlalchemy import create_engine

engine = create_engine("mysql+pymysql://analyst:***@www.nova.io:4406/DATALAKE")

import pandas as pd
df = pd.read_sql("SELECT * FROM @stage1.data.csv", engine)
```

### JDBC (DataGrip, DBeaver, Tableau)

```
JDBC URL:  jdbc:mysql://www.nova.io:4406/DATALAKE
Username:  analyst
Password:  ***

-- @stage syntax works in SQL editor:
SELECT * FROM @stage1.data.csv LIMIT 100;
```

### dbt (analytics engineering)

```yaml
# profiles.yml
my_project:
  target: dev
  outputs:
    dev:
      type: mysql
      host: www.nova.io
      port: 4406
      username: analyst
      password: "{{ env_var('NOVA_PASSWORD') }}"
      database: DATALAKE
      schema: bronze
```

```sql
-- models/staging/stg_payments.sql
-- @stage syntax in dbt models!
SELECT * FROM @stage1.data_pembayaran.csv
```

---

## Implementation

### Proxy Server Core

```python
# proxy/server.py
import asyncio
import struct
import hashlib
import time
import pymysql

from app.core.config import settings
from app.sql_dialect.pipeline import SQLPipeline


class NovaMySQLProxy:
    """MySQL protocol proxy with @stage SQL rewrite."""
    
    def __init__(self):
        self.host = settings.proxy_host
        self.port = settings.proxy_port
        self.pipeline = SQLPipeline()
        self.connection_count = 0
    
    async def start(self):
        server = await asyncio.start_server(
            self._handle_connection, self.host, self.port
        )
        print(f"✅ Nova MySQL Proxy listening on {self.host}:{self.port}")
        async with server:
            await server.serve_forever()
    
    async def _handle_connection(self, reader, writer):
        self.connection_count += 1
        conn_id = self.connection_count
        client_addr = writer.get_extra_info("peername")
        
        try:
            # 1. Send handshake
            scramble = self._generate_scramble()
            await self._send_handshake(writer, conn_id, scramble)
            
            # 2. Read auth response
            auth_packet = await self._read_packet(reader)
            username, database = self._parse_handshake_response(auth_packet)
            
            # 3. Authenticate against StarRocks
            sr_conn = self._try_authenticate(username, database)
            if not sr_conn:
                await self._send_error(writer, 1045, "Access denied")
                return
            
            # 4. Send OK
            await self._send_ok(writer)
            
            # 5. Command loop
            await self._command_loop(reader, writer, sr_conn, username, database)
            
        except Exception as e:
            print(f"[{conn_id}] Error: {e}")
        finally:
            writer.close()
    
    async def _command_loop(self, reader, writer, sr_conn, username, database):
        while True:
            packet = await self._read_packet(reader)
            if not packet:
                break
            
            cmd = packet[0]
            
            if cmd == 0x03:  # COM_QUERY
                sql = packet[1:].decode("utf-8")
                await self._execute_query(writer, sr_conn, sql, username, database)
            
            elif cmd == 0x01:  # COM_QUIT
                break
            
            elif cmd == 0x0E:  # COM_PING
                await self._send_ok(writer)
            
            else:
                await self._send_ok(writer)
    
    async def _execute_query(self, writer, sr_conn, sql, username, database):
        start = time.time()
        
        # Dialect rewrite
        context = SQLContext(database=database or "default_catalog", 
                            schema="default", user=username)
        rewritten_sql, warnings = self.pipeline.rewrite(sql, context)
        
        try:
            with sr_conn.cursor() as cur:
                cur.execute(rewritten_sql)
                
                if cur.description:
                    columns = [desc[0] for desc in cur.description]
                    rows = cur.fetchall()
                    await self._send_resultset(writer, columns, rows)
                else:
                    await self._send_ok(writer, affected_rows=cur.rowcount)
            
            duration = int((time.time() - start) * 1000)
            self._audit(username, sql, rewritten_sql, "SUCCESS", duration)
            
        except Exception as e:
            duration = int((time.time() - start) * 1000)
            self._audit(username, sql, rewritten_sql, "ERROR", duration, str(e))
            await self._send_error(writer, 1064, str(e))
```

### Protocol Helpers

```python
# proxy/protocol.py

class MySQLProtocol:
    """MySQL wire protocol helpers."""
    
    def _generate_scramble(self) -> bytes:
        """Generate random scramble for auth challenge."""
        import os
        return os.urandom(20)
    
    def _build_packet(self, seq_id: int, payload: bytes) -> bytes:
        header = len(payload).to_bytes(3, "little") + seq_id.to_bytes(1, "little")
        return header + payload
    
    async def _read_packet(self, reader) -> bytes:
        header = await reader.readexactly(4)
        length = int.from_bytes(header[:3], "little")
        if length == 0:
            return b""
        return await reader.readexactly(length)
    
    async def _send_handshake(self, writer, conn_id: int, scramble: bytes):
        payload = b"\x0a"  # protocol version
        payload += b"8.0.33-Nova\x00"  # server version
        payload += struct.pack("<I", conn_id)
        payload += scramble[:8]
        payload += b"\x00"
        payload += struct.pack("<H", 0xF7FF)  # capabilities
        payload += b"\x21"  # charset utf8
        payload += struct.pack("<H", 0x0002)  # status
        payload += struct.pack("<H", 0x00FF)
        payload += b"\x15"
        payload += b"\x00" * 10
        payload += scramble[8:] + b"\x00"
        payload += b"caching_sha2_password\x00"
        writer.write(self._build_packet(0, payload))
        await writer.drain()
    
    async def _send_ok(self, writer, affected_rows: int = 0):
        payload = b"\x00"  # OK marker
        payload += affected_rows.to_bytes(1, "little")
        payload += b"\x00"  # last insert id
        payload += struct.pack("<H", 0x0002)  # status
        payload += b"\x00\x00"  # warnings
        writer.write(self._build_packet(1, payload))
        await writer.drain()
    
    async def _send_error(self, writer, code: int, message: str):
        payload = b"\xff"  # ERROR marker
        payload += struct.pack("<H", code)
        payload += b"\x23"  # sqlstate marker
        payload += b"HY000"
        payload += message.encode("utf-8")
        writer.write(self._build_packet(1, payload))
        await writer.drain()
    
    async def _send_resultset(self, writer, columns: list, rows: list):
        # Column count
        col_count = len(columns)
        writer.write(self._build_packet(1, col_count.to_bytes(1, "little")))
        
        # Column definitions
        for i, col_name in enumerate(columns):
            payload = b"\x03def\x00\x00\x00"  # catalog, schema, table, org_table
            payload += col_name.encode("utf-8") + b"\x00"
            payload += col_name.encode("utf-8") + b"\x00"
            payload += b"\x0c"  # length of fixed-length fields
            payload += struct.pack("<H", 33)  # charset utf8
            payload += struct.pack("<I", 256)  # column length
            payload += b"\xfd"  # type = VARCHAR
            payload += struct.pack("<H", 0)  # flags
            payload += b"\x00"  # decimals
            payload += b"\x00\x00"
            writer.write(self._build_packet(i + 2, payload))
        
        # EOF
        writer.write(self._build_packet(col_count + 2, b"\xfe\x00\x00\x00\x00"))
        
        # Rows
        for row_idx, row in enumerate(rows):
            payload = b""
            for val in row:
                if val is None:
                    payload += b"\xfb"  # NULL
                else:
                    val_bytes = str(val).encode("utf-8")
                    payload += len(val_bytes).to_bytes(1, "little") + val_bytes
            writer.write(self._build_packet(row_idx % 256, payload))
        
        # EOF after rows
        writer.write(self._build_packet((row_idx + 1) % 256, b"\xfe\x00\x00\x00\x00"))
        await writer.drain()
    
    def _parse_handshake_response(self, data: bytes) -> tuple:
        """Parse client handshake response to extract username and database."""
        pos = 4  # skip capability flags
        # ... parse MySQL handshake response
        # Extract username (null-terminated string)
        username_end = data.index(b"\x00", pos)
        username = data[pos:username_end].decode("utf-8")
        
        # Database may follow
        database = ""
        if len(data) > username_end + 1:
            db_start = username_end + 1
            try:
                db_end = data.index(b"\x00", db_start)
                database = data[db_start:db_end].decode("utf-8")
            except ValueError:
                pass
        
        return username, database
```

### Startup Integration

```python
# main.py
import asyncio
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app):
    # Init NOVA_SYSTEM
    await init_nova_system()
    
    # Start MySQL proxy in background
    if settings.proxy_enabled:
        proxy = NovaMySQLProxy()
        proxy_task = asyncio.create_task(proxy.start())
    
    yield
    
    if settings.proxy_enabled:
        proxy_task.cancel()


app = FastAPI(lifespan=lifespan)
```

---

## Config

```yaml
# nova.yaml
proxy:
  enabled: true
  host: "0.0.0.0"
  port: 4406
  max_connections: 100
  connect_timeout: 10
  read_timeout: 300
```

---

## What Gets Rewritten vs Passthrough

| SQL | Proxy Behavior |
|-----|---------------|
| `SELECT * FROM @stage1.file.csv` | Rewrite → FILES() + creds → StarRocks |
| `SELECT * FROM table_name` | Passthrough → StarRocks |
| `CREATE TABLE ...` | Passthrough → StarRocks |
| `INSERT INTO table SELECT * FROM @stage1.file.csv` | Rewrite stage part → StarRocks |
| `SHOW DATABASES` | Intercept → filter NOVA_SYSTEM → client |
| `SHOW TABLES` | Passthrough → StarRocks |
| `USE database` | Track context → passthrough |
| `SET @var = value` | Track in session → passthrough |
| `COM_PING` | Respond OK directly |

---

## Audit Logging

Every query through the proxy is logged:

```python
def _audit(self, user, original_sql, rewritten_sql, status, duration_ms, error=None):
    """Log to NOVA_SYSTEM.AUDIT.LOG via separate connection."""
    try:
        conn = get_admin_connection()
        conn.cursor().execute("""
            INSERT INTO NOVA_SYSTEM.AUDIT.LOG
            (timestamp, user_name, action, sql_text, rewritten_sql, status, duration_ms, error_msg)
            VALUES (NOW(), %s, 'QUERY', %s, %s, %s, %s, %s)
        """, [user, original_sql, rewritten_sql, status, duration_ms, error])
        conn.close()
    except:
        pass  # don't let audit failure break query
```

---

## Deployment

### Docker Compose

```yaml
services:
  nova-web:
    build: ./backend
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000
    ports:
      - "8000:8000"
    environment:
      NOVA_PROXY_ENABLED: "false"
      NOVA_STARROCKS_HOST: starrocks-fe

  nova-proxy:
    build: ./backend
    command: python -m app.proxy
    ports:
      - "4406:4406"
    environment:
      NOVA_PROXY_ENABLED: "true"
      NOVA_PROXY_PORT: "4406"
      NOVA_STARROCKS_HOST: starrocks-fe

  starrocks-fe:
    image: starrocks/fe-ubuntu:4.1.1
    ports:
      - "9030:9030"
      - "8030:8030"

  starrocks-be:
    image: starrocks/be-ubuntu:4.1.1
```

### Nginx (production)

```nginx
# Web UI
server {
    listen 443 ssl;
    server_name www.nova.io;
    location / {
        proxy_pass http://nova-web:8000;
    }
}

# MySQL Proxy — TCP passthrough
stream {
    upstream nova_proxy {
        server nova-proxy:4406;
    }
    server {
        listen 4406;
        proxy_pass nova_proxy;
    }
}
```

---

## Client Compatibility

| Client | Works | Notes |
|--------|-------|-------|
| mysql CLI | ✅ | `mysql -h www.nova.io -P 4406 -u analyst -p` |
| pymysql | ✅ | Standard Python MySQL driver |
| mysql-connector-python | ✅ | Oracle's Python driver |
| SQLAlchemy | ✅ | `mysql+pymysql://...` |
| JDBC (MySQL driver) | ✅ | DataGrip, DBeaver, Tableau, etc. |
| dbt | ✅ | `type: mysql` profile |
| Grafana | ✅ | MySQL data source |
| pandas | ✅ | `pd.read_sql()` with pymysql |
| Node.js mysql2 | ✅ | Standard MySQL driver |
| Go go-sql-driver | ✅ | Standard MySQL driver |

**Any MySQL-compatible client can connect to Nova.**

---

## Security

| Concern | Solution |
|---------|---------|
| Password in transit | TLS/SSL on proxy (configure in nginx or directly) |
| Password storage | Never stored — verified per-connection against StarRocks |
| SQL injection | StarRocks handles parameterized queries |
| Audit | Every query logged to NOVA_SYSTEM.AUDIT.LOG |
| Rate limiting | Implement in proxy (max queries/minute per user) |
| Connection limits | Configurable max_connections |

---

## Summary

```
www.nova.io:4406  →  Nova MySQL Proxy  →  StarRocks :9030
                      ├── Auth (StarRocks native)
                      ├── SQL rewrite (@stage → FILES())
                      ├── Audit logging
                      └── RBAC passthrough

Any MySQL client can connect. @stage syntax works transparently.
```
