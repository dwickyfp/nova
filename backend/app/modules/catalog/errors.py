"""Errors and identifier checks shared by the catalog connectors and sources."""

from __future__ import annotations

import re

#: Catalog identifiers are interpolated into engine statements on both the
#: caller's connection and the system pool, so they are validated at every entry
#: point: a URL path segment is not trusted because a create body was validated.
_CATALOG_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class CatalogError(ValueError):
    """A catalog operation Nova refused before or after the engine."""


def safe_catalog_name(name: str) -> str:
    """Return ``name`` if it is a bare catalog identifier, else raise."""
    if not _CATALOG_NAME.match(name or ""):
        raise CatalogError(f"Invalid catalog name: {name!r}")
    return name
