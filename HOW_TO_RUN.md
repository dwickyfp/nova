# Menjalankan Nova

Panduan ini menjelaskan cara menjalankan seluruh environment development Nova:

- Infrastructure: StarRocks, Redis, dan object storage
- Backend FastAPI: port `8000`
- Frontend React/Vite: port `5173`

## Prasyarat

Pastikan software berikut sudah tersedia:

- Docker dan Docker Compose
- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Node.js
- [pnpm](https://pnpm.io/)

Periksa instalasi:

```bash
docker --version
docker compose version
python3 --version
uv --version
node --version
pnpm --version
```

Semua perintah di bawah diasumsikan dijalankan dari root project:

```bash
cd /Users/dwickyferiansyahputra/Public/Research/nova
```

---

## 1. Jalankan Infrastructure

Backend Nova membutuhkan StarRocks, Redis, dan object storage sebelum dapat
berjalan.

```bash
cd docker
cp .env.example .env
docker compose -f docker-compose-engine.yml up -d
```

Tunggu hingga seluruh container sehat:

```bash
docker compose -f docker-compose-engine.yml ps
```

Container utama yang seharusnya aktif:

- `nova-starrocks-fe`
- `nova-starrocks-be`
- `nova-minio`
- `nova-redis`

Pantau proses startup bila diperlukan:

```bash
docker compose -f docker-compose-engine.yml logs -f
```

Tekan `Ctrl+C` untuk keluar dari tampilan log. Container akan tetap berjalan.

### Verifikasi infrastructure

```bash
curl http://localhost:8030/api/health
curl http://localhost:8040/api/health
```

Alamat development:

| Service | Address |
|---|---|
| StarRocks FE | `http://localhost:8030` |
| StarRocks MySQL | `localhost:9030` |
| StarRocks BE | `http://localhost:8040` |
| Object Storage API | `http://localhost:9000` |
| Object Storage Console | `http://localhost:9001` |
| Redis | `localhost:6379` |

---

## 2. Jalankan Backend

Buka terminal baru:

```bash
cd /Users/dwickyferiansyahputra/Public/Research/nova/backend
```

### Siapkan environment

Untuk instalasi pertama:

```bash
cp .env.example .env
uv sync
```

Pastikan nilai pada `backend/.env` sesuai dengan nilai yang digunakan pada
`docker/.env`.

Contoh konfigurasi development:

```dotenv
STARROCKS_HOST=localhost
STARROCKS_FE_MYSQL_PORT=9030
STARROCKS_HTTP_PORT=8030
STARROCKS_ROOT_USER=root
STARROCKS_ROOT_PASSWORD=

REDIS_URL=redis://:nova_redis_2026@localhost:6379/0

SECRET_KEY=change-me-in-local-development
# Boleh kosong untuk development; backend membuat key sementara saat startup.
FERNET_KEY=

S3_ENDPOINT=http://localhost:9000
S3_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE
S3_SECRET_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
S3_BUCKET=stages

CORS_ORIGINS=["http://localhost:5173"]
DEBUG=true
LOG_LEVEL=INFO
```

Nilai Redis dan object storage harus sama dengan:

- `REDIS_PASSWORD`
- `MINIO_ACCESS_KEY`
- `MINIO_SECRET_KEY`

yang terdapat di `docker/.env`.

Untuk menggunakan Fernet key yang persisten:

```bash
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Salin hasilnya ke `FERNET_KEY` pada `backend/.env`.

> Konfigurasi di atas hanya contoh untuk development lokal. Jangan gunakan
> secret development di production.

### Start FastAPI

```bash
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Backend tersedia di:

- API: [http://localhost:8000](http://localhost:8000)
- Health: [http://localhost:8000/health](http://localhost:8000/health)
- Swagger UI: [http://localhost:8000/docs](http://localhost:8000/docs)

Verifikasi:

```bash
curl http://localhost:8000/health
```

Respons yang diharapkan:

```json
{
  "status": "ok",
  "version": "0.1.0"
}
```

Biarkan terminal backend tetap berjalan.

---

## 3. Jalankan Frontend

Buka terminal baru:

```bash
cd /Users/dwickyferiansyahputra/Public/Research/nova/frontend
```

Install dependency:

```bash
pnpm install
```

Jalankan development server:

```bash
pnpm dev
```

Frontend tersedia di:

[http://localhost:5173](http://localhost:5173)

Vite secara otomatis meneruskan request `/api` ke backend:

```text
http://localhost:5173/api/*
              ↓
http://localhost:8000/api/*
```

Karena itu, backend harus tetap berjalan pada port `8000`.

---

## 4. Login ke Nova

Buka:

[http://localhost:5173/sign-in](http://localhost:5173/sign-in)

Gunakan user StarRocks:

```text
Username: nova_admin
Password: nova
```

Nova melakukan autentikasi langsung ke StarRocks. Nova tidak memiliki tabel
user terpisah.

Password default `nova_admin` hanya untuk setup development awal dan harus
diganti ketika diminta.

---

## Urutan Startup Harian

Setelah dependency dan file `.env` selesai disiapkan, gunakan tiga terminal.

### Terminal 1 — Infrastructure

```bash
cd /Users/dwickyferiansyahputra/Public/Research/nova/docker
docker compose -f docker-compose-engine.yml up -d
```

### Terminal 2 — Backend

```bash
cd /Users/dwickyferiansyahputra/Public/Research/nova/backend
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Terminal 3 — Frontend

```bash
cd /Users/dwickyferiansyahputra/Public/Research/nova/frontend
pnpm dev
```

Kemudian buka:

```text
http://localhost:5173
```

---

## Menghentikan Nova

Hentikan frontend dan backend dengan `Ctrl+C` pada terminal masing-masing.

Hentikan infrastructure:

```bash
cd /Users/dwickyferiansyahputra/Public/Research/nova/docker
docker compose -f docker-compose-engine.yml down
```

Perintah tersebut mempertahankan volume dan data.

Untuk menghapus container beserta seluruh volume development:

```bash
docker compose -f docker-compose-engine.yml down -v
```

> `down -v` menghapus metadata StarRocks, data tabel, object storage, dan data
> Redis. Gunakan hanya jika benar-benar ingin reset total.

---

## Development Checks

### Backend

```bash
cd backend
uv run pytest
uv run ruff check .
```

### Frontend

```bash
cd frontend
pnpm build
pnpm lint
```

Test frontend menggunakan Playwright. Install Chromium sebelum menjalankannya:

```bash
pnpm test:browser:install
pnpm test
```

---

## Troubleshooting

### Port sudah digunakan

Periksa proses yang memakai port:

```bash
lsof -i :5173
lsof -i :8000
lsof -i :9030
```

Frontend Nova harus menggunakan `5173`, sedangkan backend menggunakan `8000`.

### Backend gagal terhubung ke StarRocks

Periksa container:

```bash
cd docker
docker compose -f docker-compose-engine.yml ps
docker logs nova-starrocks-fe --tail 100
docker logs nova-starrocks-be --tail 100
```

StarRocks membutuhkan waktu lebih lama daripada service lain ketika startup
pertama.

### Backend gagal terhubung ke Redis

Pastikan password di `backend/.env` sama dengan `REDIS_PASSWORD` pada
`docker/.env`.

Format URL Redis dengan password:

```dotenv
REDIS_URL=redis://:PASSWORD@localhost:6379/0
```

### Login menghasilkan network error

Periksa backend:

```bash
curl http://localhost:8000/health
```

Kemudian pastikan frontend dijalankan melalui `pnpm dev`, karena proxy `/api`
dikonfigurasi oleh Vite.

### Perubahan frontend tidak tampil

Hentikan lalu jalankan kembali Vite:

```bash
pnpm dev
```

Jika dependency berubah:

```bash
pnpm install
```

### Reset environment lokal

```bash
cd docker
docker compose -f docker-compose-engine.yml down -v
docker compose -f docker-compose-engine.yml up -d
```

Perintah tersebut menghapus seluruh data development sebelumnya.
