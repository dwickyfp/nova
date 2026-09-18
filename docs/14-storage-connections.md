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
| Access key | `nova.yaml` + `.env`, or external secret store | ❌ |
| Secret key | `nova.yaml` + `.env`, or external secret store | ❌ |
| Secret reference | `nova.yaml` (`secret_ref`) — the reference only | ❌ |
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

## Secret References (external secret stores)

A connection can name its credential in an external secret store instead of
carrying the value in `nova.yaml`. Nova persists the **reference**, never the
value. AWS Secrets Manager is the first supported provider.

```yaml
storage:
  connections:
    aws_prod:
      type: s3
      endpoint: ""
      bucket: my-s3-bucket
      region: us-east-1
      path_style: false
      ssl: true
      secret_ref: arn:aws:secretsmanager:us-east-1:123456789012:secret:nova/s3-prod
      # access_key / secret_key are intentionally omitted — the reference is
      # the only credential source for this connection.
```

Rules:

- **Reference-only.** `secret_ref` is the durable artefact. No secret value is
  written to `nova.yaml`, `NOVA_SYSTEM`, an audit row, a response, or a log.
- **Fail-closed.** If the provider errors, times out, or returns an
  unparseable payload, resolution raises and the query fails with a redacted
  error. Nova **never** falls back to the inline `nova.yaml` credentials — a
  connection that opted into a reference must not silently authenticate as a
  different principal.
- **No stale cache.** A short in-process TTL cache collapses repeated lookups
  within a query. A failure is not cached, so the next call retries the
  provider rather than serving a value that could not be refreshed.
- **Audited fact.** Each resolution writes an audit row
  (`event_type = 'secret_fetch'`) carrying the provider and reference — never a
  value.
- **Default path unchanged.** A connection without `secret_ref` reads
  `access_key`/`secret_key` from `nova.yaml` exactly as before; no provider is
  contacted.

### Accepted payload shapes (AWS Secrets Manager)

| Shape | Example |
|-------|---------|
| JSON object | `{"access_key": "…", "secret_key": "…", "session_token": "…"}` |
| JSON with id aliases | `{"access_key_id": "…", "secret_key_id": "…"}` |
| Plain pair | `<access_key>:<secret_key>` |

Credentials come from the standard boto3 chain (env vars, instance profile,
`AWS_PROFILE`); the adapter constructs no client until a reference is resolved,
so importing Nova never requires AWS credentials.

References may be prefixed with a scheme (`aws://my-secret`) or passed as a bare
ARN/name, which defaults to AWS Secrets Manager. Azure Key Vault and GCP Secret
Manager are **not** implemented yet.

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
