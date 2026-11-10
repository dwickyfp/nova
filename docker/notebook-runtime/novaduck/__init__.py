"""``import novaduck``: this notebook's DuckDB, connected to Nova.

See :mod:`nova_runtime.novaduck` for what the connection adds.
"""

from nova_runtime.novaduck import NovaConnection, NovaError, connect

__all__ = ["NovaConnection", "NovaError", "connect"]
