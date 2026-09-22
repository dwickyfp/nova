FROM python:3.11.13-slim AS builder
ARG UV_VERSION=0.8.17
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "uv==${UV_VERSION}"
WORKDIR /opt/nova/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.11.13-slim
WORKDIR /opt/nova/backend
COPY --from=builder /opt/nova/backend/.venv /opt/nova/backend/.venv
COPY backend/ ./
COPY docs/ /opt/nova/docs/
COPY docker/backend-entrypoint.sh /usr/local/bin/nova-backend-entrypoint
RUN chmod 0555 /usr/local/bin/nova-backend-entrypoint
ENV PATH="/opt/nova/backend/.venv/bin:${PATH}"
EXPOSE 8000 4406
ENTRYPOINT ["nova-backend-entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
