# Module 18: Authentication & Authorization

> Native StarRocks authentication. All login users ARE StarRocks users.
> First login forces password change. RBAC enforced by StarRocks.

---

## Auth Model

| User | Password | Purpose | Access |
|------|----------|---------|--------|
| `root` | (empty) | Docker internal (FE↔BE) | Docker network only, NOT exposed |
| `nova_admin` | `nova` → user changes on first login | Application admin | Full access |
| Other users | Created by nova_admin | Per-role access | RBAC enforced by StarRocks |

**Critical:** Nova has NO separate user table. StarRocks IS the user database.

---

## First Login Flow

```
Step 1: Docker starts
  starrocks-init → CREATE USER 'nova_admin' IDENTIFIED BY 'nova'
                 → GRANT ALL ON *.* TO USER 'nova_admin'

Step 2: User opens Nova (http://localhost:3000)
  Login: nova_admin / nova (default password)

Step 3: API detects first login
  Check: NOVA_SYSTEM.CONFIG_USER_PREFERENCES
         WHERE user_name = '__system__' AND pref_key = 'setup_complete'
  Result: not found → SETUP_REQUIRED

Step 4: Force password change
  UI shows setup wizard:
  ┌─────────────────────────────────────────────┐
  │  Welcome to Nova!                            │
  │  Please set a new admin password.            │
  │                                              │
  │  New Password:     [••••••••••••]            │
  │  Confirm Password: [••••••••••••]            │
  │                                              │
  │  [Set Password & Continue]                   │
  └─────────────────────────────────────────────┘

Step 5: Password changed
  API runs: ALTER USER 'nova_admin' IDENTIFIED BY 'new_password'
  API sets: setup_complete = true in NOVA_SYSTEM
  User redirected to Nova dashboard

Step 6: Create other users
  As nova_admin:
    CREATE USER 'analyst' IDENTIFIED BY '***';
    GRANT SELECT ON ALL TABLES IN ALL DATABASES TO USER 'analyst';
```

---

## Authentication Implementation

### Login Endpoint

```python
@router.post("/api/v1/auth/login")
async def login(username: str, password: str):
    # 1. Authenticate against StarRocks
    try:
        conn = pymysql.connect(
            host=settings.starrocks_host,
            port=settings.starrocks_port,
            user=username,
            password=password,
            charset="utf8mb4",
            connect_timeout=5,
        )
        conn.close()
    except pymysql.err.OperationalError:
        raise HTTPException(401, "Invalid credentials")

    # 2. Check if setup is required
    is_setup = is_system_setup_complete()

    # 3. Block default password after setup
    if is_setup and password == "nova" and username == "nova_admin":
        raise HTTPException(403, "Default password must be changed")

    # 4. Force setup on first login
    if not is_setup and username == "nova_admin":
        session_id = create_session(username, password)
        return {
            "status": "SETUP_REQUIRED",
            "session_id": session_id,
            "message": "First login — set a new admin password",
        }

    # 5. Normal authenticated session
    session_id = create_session(username, password)
    return {
        "status": "AUTHENTICATED",
        "session_id": session_id,
        "user": username,
    }
```

### Setup Endpoint

```python
@router.post("/api/v1/auth/setup")
async def setup(session_id: str, new_password: str, confirm_password: str):
    session = get_session(session_id)

    # Only nova_admin can run setup
    if session.username != "nova_admin":
        raise HTTPException(403, "Only nova_admin can run setup")

    # Setup already done?
    if is_system_setup_complete():
        raise HTTPException(400, "Setup already complete")

    # Passwords match?
    if new_password != confirm_password:
        raise HTTPException(400, "Passwords do not match")

    # Password strength?
    if len(new_password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    # 1. Change password in StarRocks
    execute_as_root(f"ALTER USER 'nova_admin' IDENTIFIED BY '{escape(new_password)}'")

    # 2. Update session with new password
    update_session_password(session_id, new_password)

    # 3. Mark setup complete
    execute_as_root("""
        INSERT INTO NOVA_SYSTEM.CONFIG_USER_PREFERENCES
        (user_name, pref_key, pref_value, updated_at)
        VALUES ('__system__', 'setup_complete', 'true', NOW())
    """)

    return {"status": "SETUP_COMPLETE", "message": "Password changed. Welcome to Nova!"}
```

### System Setup Check

```python
def is_system_setup_complete() -> bool:
    """Check if the initial setup has been completed."""
    try:
        result = execute_as_root("""
            SELECT pref_value FROM NOVA_SYSTEM.CONFIG_USER_PREFERENCES
            WHERE user_name = '__system__' AND pref_key = 'setup_complete'
        """)
        return result.get("rows") and result["rows"][0][0] == "true"
    except Exception:
        return False  # Table doesn't exist yet → setup not done
```

---

## User Management

### Create User (Admin only)

```python
@router.post("/api/v1/users/create")
async def create_user(
    request: Request,
    username: str,
    password: str,
    role: str = "read_only"
):
    session = get_session(request)

    # Only admin can create users
    if session.username != "nova_admin":
        raise HTTPException(403, "Only admin can create users")

    if not is_system_setup_complete():
        raise HTTPException(400, "Complete setup first")

    # Create user in StarRocks
    conn = get_starrocks_connection(session)
    with conn.cursor() as cur:
        cur.execute(f"CREATE USER '{username}' IDENTIFIED BY '{password}'")
        cur.execute(f"GRANT {role} TO USER '{username}'")

    return {"status": "USER_CREATED", "username": username, "role": role}
```

### List Users (Admin only)

```python
@router.get("/api/v1/users")
async def list_users(request: Request):
    session = get_session(request)

    if session.username != "nova_admin":
        raise HTTPException(403, "Admin only")

    conn = get_starrocks_connection(session)
    with conn.cursor() as cur:
        cur.execute("SELECT USER, HOST, AUTH_PLUGIN FROM mysql.user")
        users = cur.fetchall()

    # Don't expose root
    users = [u for u in users if u["USER"] != "root"]

    return {"users": users}
```

---

## RBAC Enforcement

StarRocks RBAC is enforced automatically — Nova UI adapts based on privileges.

```python
# Query user privileges
def get_user_privileges(session: UserSession) -> dict:
    conn = get_starrocks_connection(session)
    with conn.cursor() as cur:
        cur.execute("SHOW GRANTS")
        grants = cur.fetchall()

    grants_text = str(grants)
    return {
        "can_select": "SELECT" in grants_text or "ALL" in grants_text,
        "can_insert": "INSERT" in grants_text or "ALL" in grants_text,
        "can_create": "CREATE" in grants_text or "ALL" in grants_text,
        "can_drop": "DROP" in grants_text or "ALL" in grants_text,
        "can_alter": "ALTER" in grants_text or "ALL" in grants_text,
    }
```

### UI Adapts to Privileges

```
admin (GRANT ALL):
  [Create Table] [Drop Table] [Insert Data] [Create User] [All Features]

analyst (GRANT SELECT):
  [Browse Tables] [Run Queries] [View Results]
  (Create/Insert/Drop buttons hidden)
```

---

## ACCOUNTADMIN Guardrails

**ACCOUNTADMIN is the system admin role. It MUST NEVER be dropped, renamed, or revoked.**

### Why

`ACCOUNTADMIN` is created with `WITH GRANT OPTION` — the only role that can grant privileges. If dropped:
- No user can grant privileges to others
- No new users can be given meaningful access
- System is effectively locked

### Backend Interceptor

```python
# services/sql_guard.py

BLOCKED_ROLE_PATTERNS = [
    r"DROP\s+ROLE\s+ACCOUNTADMIN",
    r"REVOKE\s+.*\s+FROM\s+ROLE\s+ACCOUNTADMIN",
    r"ALTER\s+ROLE\s+ACCOUNTADMIN",
]

def guard_sql(sql: str) -> None:
    """Block dangerous operations on system objects."""
    upper = sql.upper().strip()
    for pattern in BLOCKED_ROLE_PATTERNS:
        if re.match(pattern, upper, re.IGNORECASE):
            raise HTTPException(
                403,
                "ACCOUNTADMIN role cannot be dropped, renamed, or revoked."
            )
```

### Admin UI

- ACCOUNTADMIN role row shows NO delete/edit button
- Tooltip: "System role — cannot be modified"

---

## Security Rules

| Rule | Implementation |
|------|---------------|
| Root not exposed externally | 9030 NOT mapped to host |
| Default password blocked | API rejects 'nova' after setup |
| Password strength | Minimum 8 characters |
| Session encryption | Fernet encryption for stored passwords |
| No credential in API responses | Passwords never returned in JSON |
| Audit logging | Every login attempt logged |
| ACCOUNTADMIN immutable | Backend interceptor blocks DROP/REVOKE on ACCOUNTADMIN |

---

## MySQL Proxy Auth

The MySQL proxy (port 4406) also authenticates against StarRocks:

```bash
# Connect via Nova MySQL Proxy
mysql -h localhost -P 4406 -u nova_admin -p

# Same auth flow:
# 1. Proxy receives username + password
# 2. Proxy tries pymysql.connect(host=starrocks-fe, user=username, password=password)
# 3. Success → proxy forwards queries
# 4. Failure → "Access denied"
```

---

## LDAP Support (Future)

StarRocks supports LDAP authentication. Same login form, StarRocks handles LDAP:

```sql
-- Enable LDAP
SET GLOBAL enable_authentication_from_ldap = true;
SET GLOBAL ldap_server_host = "ldap.example.com";
SET GLOBAL ldap_server_port = 389;
SET GLOBAL ldap_user_basedn = "ou=users,dc=example,dc=com";
```

Nova doesn't need changes — StarRocks handles LDAP transparently.
