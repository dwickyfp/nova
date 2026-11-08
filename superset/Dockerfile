# Nova's Superset images (docs/arch-17-superset.md): the pinned upstream release
# plus database drivers and Nova's integration package. No Superset source is
# vendored or modified here.
#
# Two targets (docs/operations/superset-lean-deployment.md):
#   web   the web app, the one-shot bootstrap and beat. They never start a
#         browser, so they carry no Chromium.
#   jobs  the Celery worker: the same release, drivers and Nova package plus
#         Playwright and Chromium, which draw thumbnails and report screenshots.
# Both share the `base` layers; Nova's files are copied last so a config change
# never reinstalls the browser. Building without --target produces `jobs`.
FROM apache/superset:6.1.0 AS base

USER root
RUN uv pip install --no-cache-dir --python /app/.venv/bin/python \
        psycopg2-binary==2.9.9 \
        PyMySQL==1.2.3 \
        starrocks==1.3.4

FROM base AS browser

RUN uv pip install --no-cache-dir --python /app/.venv/bin/python playwright==1.64.1
# The browser lives in the image, readable by the superset user.
ENV PLAYWRIGHT_BROWSERS_PATH=/app/.playwright
RUN /app/.venv/bin/playwright install --with-deps chromium \
    && chmod -R a+rX /app/.playwright \
    && rm -rf /var/lib/apt/lists/*

FROM base AS web

COPY superset/superset_config.py /app/pythonpath/superset_config.py
COPY superset/nova_superset /app/pythonpath/nova_superset
COPY --chmod=755 superset/bootstrap.sh /app/nova/bootstrap.sh
USER superset

FROM browser AS jobs

COPY superset/superset_config.py /app/pythonpath/superset_config.py
COPY superset/nova_superset /app/pythonpath/nova_superset
COPY --chmod=755 superset/bootstrap.sh /app/nova/bootstrap.sh
USER superset
