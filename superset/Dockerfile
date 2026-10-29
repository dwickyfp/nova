# Nova's Superset image (docs/arch-17-superset.md): the pinned upstream release
# plus database drivers and Nova's integration package. No Superset source is
# vendored or modified here.
FROM apache/superset:6.1.0

USER root
RUN uv pip install --no-cache-dir --python /app/.venv/bin/python \
        psycopg2-binary==2.9.9 \
        PyMySQL==1.2.3 \
        starrocks==1.3.4

COPY superset/superset_config.py /app/pythonpath/superset_config.py
COPY superset/nova_superset /app/pythonpath/nova_superset
COPY --chmod=755 superset/bootstrap.sh /app/nova/bootstrap.sh

USER superset
