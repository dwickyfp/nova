FROM ghcr.io/astral-sh/uv:0.8.17@sha256:e4644cb5bd56fdc2c5ea3ee0525d9d21eed1603bccd6a21f887a938be7e85be1 AS uv
FROM python:3.11.13-bookworm@sha256:4d88a40c18f95eacef39350c1162fa0b7846a8dd1c55311e1e5a60825b7cd6e6

# The full Python image includes the libevent/OpenSSL headers and compiler.
# Keep pooler build and tests together so their shared-library ABI is identical.
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /nova/backend
COPY backend/pyproject.toml backend/uv.lock backend/README.md backend/.python-version ./
RUN uv sync --locked --no-dev --group test --no-install-project --python /usr/local/bin/python
COPY backend/tests/integration/build_metadata_pooler.py /tmp/build_metadata_pooler.py
RUN python /tmp/build_metadata_pooler.py /opt/nova-pgbouncer \
    && chmod 755 /opt/nova-pgbouncer \
    && /opt/nova-pgbouncer/pgbouncer --version
COPY backend/app ./app
COPY backend/tests ./tests
COPY backend/metadata_migrations ./metadata_migrations
COPY docs/sql_docs/ /nova/docs/sql_docs/
COPY docs/architecture/production-refactor/inventory.json /nova/docs/architecture/production-refactor/inventory.json
ENV PATH="/nova/backend/.venv/bin:${PATH}" \
    HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    NOVA_TEST_PGBOUNCER_BINARY=/opt/nova-pgbouncer/pgbouncer
USER 1000:1000
ENTRYPOINT ["python"]
