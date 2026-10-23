FROM node:22.22.3-bookworm-slim AS ui-runtime
WORKDIR /opt/nova/frontend
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN --mount=type=cache,target=/pnpm/store \
    corepack pnpm@11.8.0 install --frozen-lockfile --store-dir=/pnpm/store \
    --fetch-timeout=180000 --network-concurrency=8
COPY frontend/ ./
RUN corepack pnpm@11.8.0 build \
    && npm install --prefix /opt/nova/report-browser --omit=dev playwright@1.59.1

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
WORKDIR /opt/nova/backend
COPY --from=builder /opt/nova/backend/.venv /opt/nova/backend/.venv
COPY --from=ui-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=ui-runtime /opt/nova/report-browser/node_modules /opt/nova/frontend/node_modules
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/nova/browsers
RUN node /opt/nova/frontend/node_modules/playwright/cli.js install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ui-runtime /opt/nova/frontend/dist /opt/nova/frontend/dist
COPY frontend/scripts/render-report-snapshot.mjs /opt/nova/frontend/scripts/render-report-snapshot.mjs
COPY backend/ ./
COPY docs/ /opt/nova/docs/
COPY docker/backend-entrypoint.sh /usr/local/bin/nova-backend-entrypoint
RUN chmod 0555 /usr/local/bin/nova-backend-entrypoint
ENV PATH="/opt/nova/backend/.venv/bin:${PATH}"
EXPOSE 8000 4406
ENTRYPOINT ["nova-backend-entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
