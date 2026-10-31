# Node runs the bundled Studio UI validator (backend/app/modules/assistant/ui_runtime).
FROM node:22.22.3-bookworm-slim AS ui-runtime

FROM python:3.11.13-slim AS builder
ARG UV_VERSION=0.8.17
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "uv==${UV_VERSION}"
WORKDIR /opt/nova/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv UV_HTTP_TIMEOUT=180 uv sync --frozen --no-dev --no-install-project

FROM python:3.11.13-slim
# PostgreSQL 16 client (matches superset-db) for BI metadata in NOVA_SYSTEM
# backups. The distribution's own client is another major version: an older
# one cannot dump a 16 server, a newer one writes settings 16 rejects on restore.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        | gpg --dearmor -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg \
    && . /etc/os-release \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-16 \
    && apt-get purge -y --auto-remove curl gnupg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/nova/backend
COPY --from=builder /opt/nova/backend/.venv /opt/nova/backend/.venv
COPY --from=ui-runtime /usr/local/bin/node /usr/local/bin/node
COPY backend/ ./
COPY docs/ /opt/nova/docs/
COPY docker/backend-entrypoint.sh /usr/local/bin/nova-backend-entrypoint
RUN chmod 0555 /usr/local/bin/nova-backend-entrypoint
ENV PATH="/opt/nova/backend/.venv/bin:${PATH}"
EXPOSE 8000 4406
ENTRYPOINT ["nova-backend-entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
