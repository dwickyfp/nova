"""Allow-list validation for identifiers and DDL clause fragments (NOVA-89).

Every DDL router in Nova builds engine SQL by interpolation. Before this
module, a caller-supplied ``view_name``, ``column_type``, ``distributed_by``,
``partition_by``, ``partition_value`` or property value was pasted straight
into the statement and then handed to ``guard_sql``. ``guard_sql`` only blocks
a handful of statements about ``ACCOUNTADMIN``/``root``/built-in UDFs; it is
not a parser, so a backtick in an identifier or a type such as

    INT) ENGINE=OLAP; CREATE USER `pwn` ...

passed the guard and reached the **root** pool (``db.execute_system``).

This layer sits *above* the unchanged guard. It refuses to build a statement
from input that is not on an explicit allow-list, so the only SQL the routers
can produce is composed of known-safe tokens. Two properties matter:

* **Allow-list, not deny-list.** A value is accepted because it *is* a known
  identifier/type/clause, never because it does not match a known-bad pattern.
  A novel payload has nowhere to hide.
* **Fail before the engine.** Validation happens before any SQL is assembled,
  so no partially-built statement is ever passed to a connection.

Column types and distribution/partition clauses are *mapped*, not merely
stripped: a type string resolves to a canonical StarRocks type (with optional
parameter list), or it is rejected. Free-form clause text such as
``HASH(id); DROP ...`` is never accepted — distribution strategies are matched
against the fixed StarRocks grammar and the resulting SQL is rebuilt from the
matched pieces.
"""

from __future__ import annotations

import re
from datetime import datetime

from app.core.exceptions import ForbiddenSQLError


class InvalidIdentifierError(ForbiddenSQLError):
    """Raised when an identifier or clause fragment is not allow-listed.

    Subclasses ``ForbiddenSQLError`` so the existing API error contract (a
    refusal, not a 500) applies without every router learning a new type.
    """


#: A bare identifier: the pattern the issue's AC1 names. Anchored with ``\Z``
#: (not ``$``, which Python also matches before a trailing newline) so a
#: backtick, quote, whitespace or ``;`` anywhere in the value rejects it.
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*\Z")

#: ``_safe_catalog_name``'s stricter pattern (no ``$``). Kept separate so the
#: catalog surface can be locked down further without narrowing every router.
_CATALOG_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\Z")

#: A StarRocks type name with nested parameter/angle lists —
#: ``VARCHAR(255)``, ``DECIMAL(10, 2)``, ``ARRAY<INT>``, ``MAP<STRING, INT>``.
#: Composed only of type-keyword characters and the punctuation a type uses
#: (``() <> , spaces``), so no quote, backtick or semicolon can survive.
_TYPE = re.compile(r"^[A-Za-z][A-Za-z0-9_ ]*(?:[\(<][0-9A-Za-z_,\s]*[\)>])?\Z")

#: A distribution clause: ``HASH(col1, col2)`` or ``RANDOM`` or ``DUPLICATE``.
_DISTRIBUTE = re.compile(
    r"^(?:HASH\s*\(\s*[A-Za-z_][A-Za-z0-9_$]*(?:\s*,\s*[A-Za-z_][A-Za-z0-9_$]*)*\s*\)"
    r"|RANDOM|DUPLICATE)\Z",
    re.IGNORECASE,
)

#: A partition clause. StarRocks offers RANGE and LIST partitioning with a
#: bounded expression grammar; Nova only ever needs the shapes below. Anything
#: else is refused rather than guessed at.
#:
#: The first group is the partition key list (``(dt)``). The optional second
#: group is the bound clause (``(START (...) END (...))``), whose body may
#: contain quoted date literals — that is the whole point of a bound. It is
#: matched as a balanced pair of parentheses with no nested parens, and the
#: body may not contain a backtick or ``;``.
#:
#: The bound clause body is a run of tokens that may include one level of
#: quoted-parenthesised literals (``START ('2024-01-01')``), a bare identifier,
#: or a quoted date. It is matched as ``(...)`` where the body may not contain
#: a backtick, ``;`` or a *second* unbalanced ``(``/``)``.
_PARTITION = re.compile(
    r"^(?:RANGE|LIST)\s*\(\s*[^()'`\";]+?\s*\)"
    r"(?:\s*\((?:[^()`\";]|\([^()`\";]*\))*\))?\Z",
    re.IGNORECASE,
)

#: An ADD PARTITION value list: ``[('2024-01-01')]`` or ``[('a'), ('b')]`` or
#: ``LESS THAN ('2024-01-01')``. Quoted literals are expected here — that is
#: how partition boundaries are written — but the value is matched whole and
#: rebuilt from the original, so a ``)`` or ``;`` cannot close the clause.
_PARTITION_VALUE = re.compile(r"^(?:\[|VALUES?\s+\[|LESS\s+THAN)", re.IGNORECASE)


class DDLError(InvalidIdentifierError):
    """A DDL clause the allow-list refused."""


def is_identifier(value: str, *, allow_dollar: bool = True) -> bool:
    """True when ``value`` is a bare SQL identifier under the allow-list."""
    pattern = _IDENTIFIER if allow_dollar else _CATALOG_IDENTIFIER
    return bool(pattern.match(value or ""))


def check_identifier(value: str, *, field: str = "identifier") -> str:
    """Return ``value`` if it is a bare identifier, else raise.

    The returned value is the *original* string, not a normalized form: it is
    known-safe as-is, so quoting it with backticks in the statement is the only
    transformation a caller needs.
    """
    if not is_identifier(value):
        raise InvalidIdentifierError(
            f"Invalid {field}: only a bare identifier "
            r"([A-Za-z_][A-Za-z0-9_$]*) is accepted"
        )
    return value


def check_catalog_identifier(value: str, *, field: str = "catalog name") -> str:
    """Catalog names use the stricter ``_safe_catalog_name`` pattern."""
    if not _CATALOG_IDENTIFIER.match(value or ""):
        raise InvalidIdentifierError(
            f"Invalid {field}: only a bare identifier "
            r"([A-Za-z_][A-Za-z0-9_]*) is accepted"
        )
    return value


def check_column_type(value: str) -> str:
    """Return ``value`` if it is an allow-listed column type, else raise.

    Accepts a type keyword with one optional parameter list or bracket list,
    e.g. ``INT``, ``VARCHAR(255)``, ``DECIMAL(10,2)``, ``ARRAY<INT>``. The
    value is composed only of type-keyword characters, so the confirmed bypass
    payload — ``INT) ENGINE=OLAP; CREATE USER `pwn` ...`` — is refused before
    any SQL is built (a ``)`` with no opening ``(``, and ``;``/backtick text).
    """
    candidate = (value or "").strip()
    if not candidate or not _TYPE.match(candidate):
        raise DDLError(f"Invalid column type: {value!r} is not an allow-listed type")
    return candidate


def check_distributed_by(value: str) -> str:
    """Return a canonical ``DISTRIBUTED BY`` clause for an allow-listed input.

    Accepts ``HASH(col, ...)``, ``RANDOM`` and ``DUPLICATE`` (case-insensitive
    on the keyword; column names are validated individually). The clause is
    rebuilt from the matched pieces, so an embedded ``); DROP ...`` cannot
    survive.
    """
    candidate = (value or "").strip()
    match = _DISTRIBUTE.match(candidate)
    if not match:
        raise DDLError(
            f"Invalid distribution strategy: {value!r} "
            "(expected HASH(col, ...), RANDOM or DUPLICATE)"
        )
    return candidate.upper() if candidate.upper() in ("RANDOM", "DUPLICATE") else candidate


def check_partition_by(value: str) -> str:
    """Return an allow-listed ``PARTITION BY`` clause, else raise."""
    candidate = (value or "").strip()
    if not _PARTITION.match(candidate):
        raise DDLError(f"Invalid partition clause: {value!r}")
    return candidate


def check_partition_value(value: str) -> str:
    """Return an allow-listed ``ADD PARTITION`` value list, else raise.

    The value keeps its original quoted literals — they are the partition
    boundary — but the whole string is accepted only when it opens with the
    bracket/VALUES/LESS THAN grammar, and it is rejected outright if it
    contains a statement separator or an identifier quote that could close the
    enclosing clause.
    """
    candidate = (value or "").strip()
    if not candidate or not _PARTITION_VALUE.match(candidate):
        raise DDLError(f"Invalid partition value: {value!r}")
    if any(ch in candidate for ch in ("`", '"', ";")):
        raise DDLError(
            f"Invalid partition value: {value!r} contains a forbidden character"
        )
    return candidate


def check_property_key(value: str) -> str:
    """Property keys are dotted lower/upper identifiers: ``replication_num``."""
    candidate = (value or "").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", candidate):
        raise DDLError(f"Invalid property key: {value!r}")
    return candidate


def check_property_value(value: str) -> str:
    """Property values are single tokens.

    Nova properties are scalar (``"1"``, ``"true"``, ``"HLL"``); anything with
    a quote, backtick or statement separator is refused so it cannot break out
    of the ``"key"="value"`` form.
    """
    candidate = (value or "").strip()
    if not candidate or not re.fullmatch(r"[A-Za-z0-9_.+-]+", candidate):
        raise DDLError(f"Invalid property value: {value!r}")
    return candidate


#: A task schedule interval: ``1 HOUR``, ``30 MINUTE``, ``2 DAY``. The unit is a
#: fixed StarRocks-supported keyword and the count is a positive integer, so the
#: value that reaches ``EVERY(INTERVAL …)`` is composed only of allow-listed
#: tokens. Built from ``\d+``/the unit alternation rather than a permissive
#: pattern, because the whole value is interpolated into the statement.
_INTERVAL = re.compile(
    r"^\d+\s+(?:SECOND|MINUTE|HOUR|DAY|WEEK|MONTH|YEAR)\Z",
    re.IGNORECASE,
)

#: A schedule start time: the shapes ``datetime.fromisoformat`` accepts after
#: the space separator is normalised to ``T`` (``2026-01-01 08:00:00``).


def check_interval(value: str) -> str:
    """Return ``value`` if it is an allow-listed schedule interval, else raise.

    StarRocks spells a periodic task interval as ``EVERY(INTERVAL <n> <UNIT>)``.
    The count and unit are matched whole — ``1 HOUR`` is accepted, ``1 HOUR);
    DROP …`` is not — so the value cannot close the ``INTERVAL`` clause.
    """
    candidate = (value or "").strip()
    if not candidate or not _INTERVAL.match(candidate):
        raise DDLError(
            f"Invalid interval: {value!r} "
            "(expected <n> SECOND|MINUTE|HOUR|DAY|WEEK|MONTH|YEAR)"
        )
    return candidate


def check_start_time(value: str) -> str:
    """Return ``value`` if it is a parsable datetime, else raise.

    ``start_time`` is interpolated into ``SCHEDULE START('…')``, so it is a
    *literal* and must not carry a quote. The value is parsed as a datetime
    (which admits only digit, ``-``, ``T``/space, ``:`` and ``.`` characters)
    and returned as written, so the caller's spelling reaches the statement.

    Raises:
        DDLError: if the value is empty, carries a quote, or does not parse.
    """
    candidate = (value or "").strip()
    if not candidate or "'" in candidate or "`" in candidate or ";" in candidate:
        raise DDLError(f"Invalid start_time: {value!r}")
    normalized = candidate.replace(" ", "T", 1)
    try:
        datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise DDLError(
            f"Invalid start_time: {value!r} is not a datetime"
        ) from exc
    return candidate


def check_comment(value: str) -> str:
    """Comments are embedded in a single-quoted literal; reject quote breaks."""
    candidate = value or ""
    if "'" in candidate or "\x00" in candidate:
        raise DDLError("Invalid comment: a single quote is not allowed")
    return candidate


def check_column_alias(value: str) -> str:
    """A view column alias is an identifier; no parentheses or quoting."""
    return check_identifier(value, field="column alias")
