# Module 14: Storage Connections

> Read-only view of storage backends configured in `nova.yaml`.
> Connections are NOT editable via UI — edit `nova.yaml` and restart.

---

## Purpose

Storage Connections define how Nova talks to object storage (S3, MinIO, Azure Blob, GCS). They are **infrastructure config**, not user data.

```
nova.yaml                → storage credentials + connection details (static)
NOVA_SYSTEM.CONFIG       → stage definitions (dynamic, user-created)
UI                       → read-only view of connections (no edit)
```

---

## Where Credentials Live

| Item | Location | Editable via UI |
|------|----------|----------------|
| Storage type | `nova.yaml` | ❌ |
| Endpoint | `nova.yaml` | ❌ |
| Bucket | `nova.yaml` | ❌ |
| Access key | `nova.yaml` + `.env` | ❌ |
| Secret key | `nova.yaml` + `.env` | ❌ |
| Stage name | `NOVA_SYSTEM.CONFIG_STAGES` | ✅ |
| Stage prefix | `NOVA_SYSTEM.CONFIG_STAGES` | ✅ |

---

## Supported Storage Types

| Type | Provider | Protocol |
|------|----------|----------|
| **minio** | MinIO | S3-compatible |
| **s3** | Amazon S3 | S3 |
| **azure_blob** | Microsoft Azure | Azure Blob API |
| **gcs** | Google Cloud | GCS API |
| **oss** | Alibaba OSS | S3-compatible |
| **ceph** | Ceph | S3-compatible |

---

## nova.yaml Example

```yaml
storage:
  connections:
    production:
      type: minio
      endpoint: http://minio:9000
      bucket: nova-stages
      access_key: ${MINIO_ACCESS_KEY}
      secret_key: ${MINIO_SECRET_KEY}
      region: ""
      path_style: true
      ssl: false

    backup:
      type: azure_blob
      endpoint: mystorageaccount
      bucket: nova-backup
      access_key: ${AZURE_STORAGE_KEY}
      secret_key: ""
      region: ""

    archive:
      type: s3
      endpoint: ""
      bucket: nova-archive-us-east-1
      access_key: ${AWS_ACCESS_KEY_ID}
      secret_key: ${AWS_SECRET_ACCESS_KEY}
      region: us-east-1
      path_style: false
      ssl: true
```

`.env` (git-ignored):
```bash
MINIO_ACCESS_KEY=AKIAIO...MPLE
MINIO_SECRET_KEY=***
AZURE_STORAGE_KEY=base64encodedkey==
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=***
```

---

## Admin UI: Read-Only View

```
┌─ Storage Connections ───────────────────────────────────┐
│                                                          │
│  ℹ️ Connections are configured in nova.yaml              │
│     Edit nova.yaml and restart Nova to modify.           │
│                                                          │
│  Name          Type      Endpoint          Status        │
│  production    MinIO     minio:9000        🟢 Connected │
│  backup        Azure     blob.core...      🟢 Connected │
│  archive       S3        s3.us-east-1...   🟢 Connected │
│                                                          │
│  [Test All Connections]                                  │
│                                                          │
│  ── Stages using each connection ──                      │
│  production: stage1 (DATALAKE.bronze), stage2 (ANALYTICS)│
│  backup: imports (ANALYTICS.raw)                         │
│  archive: (none)                                         │
│                                                          │
│  ── How to add a new connection ──                       │
│  1. Edit nova.yaml → storage.connections section         │
│  2. Add connection details (type, endpoint, bucket)      │
│  3. Add secrets to .env                                  │
│  4. Restart Nova                                         │
│  5. Create a Stage that references the new connection    │
└──────────────────────────────────────────────────────────┘
```

---

## API Endpoints

```python
# Only read operations — no CREATE/UPDATE/DELETE for connections

@router.get("/connections")
async def list_connections():
    """List all connections from nova.yaml (secrets masked)."""
    connections = []
    for name, cfg in config.storage_connections.items():
        connections.append({
            "name": name,
            "type": cfg.type,
            "endpoint": cfg.endpoint,
            "bucket": cfg.bucket,
            "region": cfg.region,
            # access_key and secret_key NOT returned
        })
    return connections


@router.post("/connections/{name}/test")
async def test_connection(name: str):
    """Test if a connection is reachable."""
    cfg = config.storage_connections.get(name)
    if not cfg:
        raise HTTPException(404, f"Connection '{name}' not found in nova.yaml")
    
    try:
        provider = StorageFactory.create(cfg)
        # Try listing root prefix
        provider.list("")
        return {"status": "connected", "connection": name}
    except Exception as e:
        return {"status": "error", "connection": name, "error": str(e)}
```

---

## Security Notes

- Credentials **never** leave the server
- API responses **never** include access_key or secret_key
- `.env` is git-ignored
- `nova.yaml` can be committed (with `${VAR}` references) — secrets in `.env`
