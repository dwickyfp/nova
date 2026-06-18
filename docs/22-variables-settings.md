# Module 22: Variables & Settings

> Manage session/global variables, password policies, and system configuration.

---

## Session & Global Variables

### Key Variables

| Variable | Scope | Description | Default |
|----------|-------|-------------|---------|
| `query_timeout` | Session/Global | Query timeout (seconds) | 300 |
| `exec_mem_limit` | Session/Global | Memory limit per query (bytes) | 2GB |
| `parallel_fragment_exec_instance_num` | Session/Global | Parallelism | auto |
| `pipeline_dop` | Session/Global | Pipeline degree of parallelism | 0 (auto) |
| `batch_size` | Session/Global | Rows per batch | 4096 |
| `sql_mode` | Session/Global | SQL mode flags | DEFAULT |
| `time_zone` | Session/Global | Time zone | Asia/Shanghai |
| `enable_profile` | Session/Global | Enable query profiling | false |
| `runtime_profile_report_interval` | Global | Profile report interval | 10 |
| `max_allowed_packet` | Global | Max packet size | 64MB |
| `auto_increment_increment` | Global | Auto-increment step | 1 |
| `enable_spill` | Session/Global | Enable disk spill | true |
| `spill_mode` | Session/Global | Spill mode | auto |
| `enable_materialized_view_rewrite` | Session/Global | MV auto-rewrite | true |
| `enable_query_queue` | Global | Enable query queue | true |
| `task_runs_concurrency` | Global | Max parallel tasks | 4 |
| `password_lifetime` | Global | Password expiry (days) | 90 |
| `password_history` | Global | Password history count | 0 |
| `failed_login_attempts` | Global | Max failed logins | 0 |
| `validate_password` | Global | Enable password validation | false |

### Operations

```sql
-- Show session variables
SHOW VARIABLES;

-- Show global variables
SHOW GLOBAL VARIABLES;

-- Search variables
SHOW VARIABLES LIKE '%timeout%';

-- Set session variable
SET SESSION query_timeout = 600;

-- Set global variable
SET GLOBAL enable_query_queue = true;

-- Reset to default
SET SESSION query_timeout = DEFAULT;
```

---

## Password Policies

```sql
-- Password expiry
SET GLOBAL password_lifetime = 90;

-- Password history (prevent reuse)
SET GLOBAL password_history = 5;

-- Failed login lockout
SET GLOBAL failed_login_attempts = 5;
SET GLOBAL password_lock_time = 30;  -- minutes

-- Password complexity
SET GLOBAL validate_password = ON;
SET GLOBAL validate_password_length = 8;
SET GLOBAL validate_password_mixed_case_count = 1;
SET GLOBAL validate_password_number_count = 1;
SET GLOBAL validate_password_special_char_count = 1;
```

---

## Nova UI

### Variables Browser

```
┌─ Variables ─────────────────────────────────────────────┐
│                                                          │
│  Scope: (●) Session  ( ) Global                         │
│  Search: [query_timeout                    ]             │
│                                                          │
│  Name                              Value    Default      │
│  query_timeout                     300      300          │
│  exec_mem_limit                    2147...  2147...      │
│  parallel_fragment_exec_inst...    0        0            │
│  pipeline_dop                      0        0            │
│  batch_size                        4096     4096         │
│  sql_mode                          DEFAULT  DEFAULT      │
│  time_zone                         Asia/..  Asia/..      │
│  enable_profile                    false    false        │
│  enable_spill                      true     true         │
│  enable_materialized_view_rewrite  true     true         │
│                                                          │
│  ⚠️ Changed values shown in bold. [Reset All to Default]│
└──────────────────────────────────────────────────────────┘
```

### Password Policy

```
┌─ Password Policy ───────────────────────────────────────┐
│                                                          │
│  Expiration                                              │
│  Password lifetime: [90 ▼] days                          │
│  Password history:  [5  ▼] passwords                     │
│                                                          │
│  Lockout                                                 │
│  Failed attempts:   [5  ▼] before lock                  │
│  Lock duration:     [30 ▼] minutes                      │
│                                                          │
│  Complexity                                              │
│  [✓] Enable validation                                   │
│  Min length:     [8  ▼]                                  │
│  Mixed case:     [1  ▼] uppercase + lowercase            │
│  Numbers:        [1  ▼] digits                           │
│  Special chars:  [1  ▼] special characters               │
│                                                          │
│  [Save]  [Reset to Defaults]                             │
└──────────────────────────────────────────────────────────┘
```

### Network Policy

```
┌─ Network Policy ────────────────────────────────────────┐
│                                                          │
│  IP Allowlist                                            │
│  ┌──────────────────────────────────────────────────┐   │
│  │ 10.0.0.0/8          Internal network             │   │
│  │ 192.168.1.0/24      Office network               │   │
│  │ 203.0.113.50        VPN gateway                  │   │
│  └──────────────────────────────────────────────────┘   │
│  [+ Add IP Range]                                       │
│                                                          │
│  IP Blocklist                                            │
│  ┌──────────────────────────────────────────────────┐   │
│  │ (empty — all non-allowlisted IPs blocked)        │   │
│  └──────────────────────────────────────────────────┘   │
│                                                          │
│  Apply to: [All users ▼] or [Specific roles]            │
│  [Save]                                                  │
└──────────────────────────────────────────────────────────┘
```
