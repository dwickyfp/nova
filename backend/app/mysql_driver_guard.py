"""Refuse mapping values as MySQL driver parameters.

asyncmy (through 0.2.11, no fixed release) escapes a ``dict`` parameter value
by escaping its values but splicing its keys into the SQL text unescaped
(PYSEC-2026-286). Nova passes positional scalars only, so a mapping value is
always a mistake: it is refused before any SQL text is built. Connections copy
the driver's conversion table when they open, so every module that opens a
connection imports this module first (enforced by tests/unit/test_mysql_driver_guard.py).
Metadata-only processes never load the driver.
"""

from __future__ import annotations

from typing import Any, NoReturn

from asyncmy import converters


def _refuse_mapping(value: Any, *_: Any) -> NoReturn:
    raise TypeError("A query parameter cannot be a mapping; pass scalar values")


converters.conversions[dict] = _refuse_mapping
