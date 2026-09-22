"""Apache Ranger integration.

Ranger is Nova's authorization policy store and the runtime authority used by
the StarRocks FE plugin.  This package is deliberately an infrastructure
adapter; Nova's security rules live in :mod:`app.modules.access_control`.
"""

from .client import RangerClient, RangerError, RangerUnavailableError

__all__ = ["RangerClient", "RangerError", "RangerUnavailableError"]
