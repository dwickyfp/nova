"""Nova MySQL protocol proxy.

Any MySQL client — CLI, JDBC, Python driver, dbt — can connect to Nova on
``PROXY_PORT`` (default 4406). The proxy:

* authenticates against StarRocks (no second credential store),
* routes every statement through ``QueryService``, so the ``@stage`` → ``FILES()``
  translation, the ACCOUNTADMIN guard, credential injection and the audit write
  all happen exactly as they do over HTTP,
* hides ``NOVA_SYSTEM`` from ``SHOW DATABASES``, and
* keeps storage credentials off the wire.

Run standalone with ``python -m app.proxy``. The FastAPI lifespan starts the
same server in-process when ``PROXY_ENABLED`` is set.
"""

from app.proxy.server import MySQLProxyServer, serve

__all__ = ["MySQLProxyServer", "serve"]
