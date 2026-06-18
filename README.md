# Nova

> Snowflake-grade management console for StarRocks.

## Quick Start

### Prerequisites
- Docker & Docker Compose
- MySQL client (for direct access)

### Start Engine

```bash
cd docker
cp .env.example .env
docker compose -f docker-compose-engine.yml up -d
```

### Verify

```bash
# Check all services
docker compose -f docker-compose-engine.yml ps

# StarRocks FE
curl http://localhost:8030/api/health

# MinIO Console
open http://localhost:9001  # minioadmin / minioadmin

# MySQL (via Docker network)
docker exec -it nova-starrocks-fe mysql -u nova_admin -p  # password: nova
```

### First Login

1. Connect: `mysql -h localhost -P 9030 -u nova_admin -p` (password: `nova`)
2. Change password when prompted
3. Create users: `CREATE USER 'analyst' IDENTIFIED BY '***';`

## Architecture

```
Nova Frontend (React)  →  Nova Backend (FastAPI)  →  StarRocks (4.1.1)
                                                    ↑
MySQL Proxy (:4406)  ──────────────────────────────┘
(any MySQL client)

Storage: MinIO (S3-compatible)
Cache:   Redis (sessions + query cache)
```

## Services

| Service | Port | Purpose |
|---------|------|---------|
| StarRocks FE | 8030 | HTTP / Web UI |
| StarRocks BE | 8040 | Backend HTTP |
| MinIO API | 9000 | S3-compatible storage |
| MinIO Console | 9001 | Storage management |
| Redis | 6379 | Session store + cache |

## Auth

| User | Password | Purpose |
|------|----------|---------|
| `root` | (empty) | Docker internal only |
| `nova_admin` | `nova` → change on first login | Application admin |
| Others | Created by `nova_admin` | Per-role access |

Role: `ACCOUNTADMIN` (full access)

## Docs

See `docs/` for complete feature specs and architecture docs.

| Category | Files |
|----------|-------|
| Feature Modules | `01-overview.md` through `27-data-sharing.md` |
| Architecture | `arch-01` through `arch-07` |
| Analysis | `gap-analysis.md` |

## Project Structure

```
nova/
├── AGENTS.md           # AI agent guide (consistency rules)
├── README.md           # This file
├── docs/               # Feature specs + architecture (35 files)
├── docker/             # Docker Compose + configs
│   ├── docker-compose-engine.yml
│   ├── .env.example
│   └── nova.yaml
├── backend/            # FastAPI (planned)
└── frontend/           # React/Next.js (planned)
```

## License

Internal project — not yet licensed for distribution.
