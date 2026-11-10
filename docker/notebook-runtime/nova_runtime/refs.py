"""``{{name}}`` references in SQL cells.

A reference names a value in the kernel: another cell's result, a pandas,
Polars or Arrow frame, or a scalar. It is a Nova SQL-cell feature, not DuckDB
syntax, so it is resolved here with a small scanner, never by string
formatting or ``eval``:

* only ``{{identifier}}`` outside string literals, quoted identifiers and
  comments is a reference;
* a table-like value becomes a registered relation under a generated name
  (``__nova_ref_<name>``), so user text never becomes SQL text;
* a scalar becomes a ``?`` parameter bound by DuckDB.
"""

from __future__ import annotations

import datetime as _dt
import decimal
import re
from dataclasses import dataclass, field
from typing import Any

from .tables import kind_of

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SCALARS = (bool, int, float, str, decimal.Decimal, _dt.date, _dt.datetime, _dt.time)
#: References one cell may hold; a guard against runaway registration.
MAX_REFS = 64


class ReferenceError_(Exception):
    """A ``{{name}}`` reference that cannot be resolved."""


@dataclass
class Resolved:
    sql: str
    params: list[Any] = field(default_factory=list)
    relations: dict[str, Any] = field(default_factory=dict)  # registered name -> value
    names: list[str] = field(default_factory=list)


def find_references(sql: str) -> list[tuple[int, int, str]]:
    """``(start, end, name)`` for each reference outside literals and comments."""
    found: list[tuple[int, int, str]] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in ("'", '"', "`"):
            quote = ch
            i += 1
            while i < n:
                if sql[i] == quote:
                    if i + 1 < n and sql[i + 1] == quote:  # doubled quote escape
                        i += 2
                        continue
                    break
                i += 1
            i += 1
        elif sql.startswith("--", i):
            end = sql.find("\n", i)
            i = n if end < 0 else end + 1
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end < 0 else end + 2
        elif sql.startswith("{{", i):
            close = sql.find("}}", i + 2)
            if close < 0:
                raise ReferenceError_("A '{{' reference is not closed with '}}'")
            name = sql[i + 2 : close].strip()
            if not _IDENT.fullmatch(name):
                raise ReferenceError_(
                    "A reference holds one name, like {{sales_by_region}}"
                )
            found.append((i, close + 2, name))
            i = close + 2
        else:
            i += 1
    if len(found) > MAX_REFS:
        raise ReferenceError_(f"A cell can reference at most {MAX_REFS} values")
    return found


def resolve(sql: str, namespace: dict[str, Any]) -> Resolved:
    """Replace references with registered relation names or ``?`` parameters."""
    refs = find_references(sql)
    if not refs:
        return Resolved(sql=sql)
    out: list[str] = []
    params: list[Any] = []
    relations: dict[str, Any] = {}
    names: list[str] = []
    last = 0
    for start, end, name in refs:
        out.append(sql[last:start])
        last = end
        if name not in namespace:
            raise ReferenceError_(f"'{name}' is not defined in this runtime; run its cell first")
        value = namespace[name]
        names.append(name)
        if kind_of(value) is not None:
            registered = f"__nova_ref_{name}"
            relations[registered] = value
            out.append(f'"{registered}"')
        elif value is None or isinstance(value, _SCALARS):
            out.append("?")
            params.append(value)
        else:
            raise ReferenceError_(
                f"'{name}' is a {type(value).__name__}; a reference must be a table "
                "(a result, DataFrame or Arrow table) or a single value"
            )
    out.append(sql[last:])
    return Resolved(sql="".join(out), params=params, relations=relations, names=names)
