# Nova Notebook runtime image.
#
# A lean Python kernel for one notebook runtime: Jupyter server + ipykernel,
# DuckDB with its preinstalled extensions, the dataframe stack and the trusted
# nova_runtime bootstrap. OpenSandbox injects execd at start; execd drives the
# kernel through the loopback Jupyter server. See docs/arch-20-nova-notebook.md.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLBACKEND=module://matplotlib_inline.backend_inline \
    NOVA_DUCKDB_EXTENSION_DIR=/opt/nova/duckdb-extensions \
    PYTHONPATH=/opt/nova/lib \
    JUPYTER_PORT=8888 \
    JUPYTER_HOST=http://127.0.0.1:8888

COPY requirements.lock /opt/nova/requirements.lock
RUN python -m pip install --require-hashes --no-deps -r /opt/nova/requirements.lock \
 && python -m ipykernel install --sys-prefix --name nova-python --display-name "Python (Nova)"

# DuckDB extensions are code: install the approved set at build time from the
# official repository; the kernel never auto-installs or loads others.
RUN python - <<'PY'
import duckdb, os
d = os.environ["NOVA_DUCKDB_EXTENSION_DIR"]
os.makedirs(d, exist_ok=True)
con = duckdb.connect(config={"extension_directory": d})
for ext in ("mysql", "parquet", "json"):
    con.install_extension(ext)
    con.load_extension(ext)
print(con.sql("select extension_name, installed, extension_version from duckdb_extensions() where installed").fetchall())
PY

COPY nova_runtime /opt/nova/lib/nova_runtime
COPY tests /opt/nova/tests
COPY entrypoint.sh /opt/nova/entrypoint.sh

RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin nova \
 && mkdir -p /workspace /tmp/nova \
 && chown -R nova:nova /workspace /tmp/nova \
 && chmod -R a-w /opt/nova/lib /opt/nova/duckdb-extensions

USER nova
WORKDIR /workspace
ENTRYPOINT ["/opt/nova/entrypoint.sh"]
