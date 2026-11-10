"""What a notebook write-back asks a source to do, and what it reports.

StarRocks reads Postgres and Snowflake through JDBC catalogs but cannot write
to them, so Nova writes to the source itself with the connection's write
credential, after Ranger allowed the caller (:mod:`..writeback`). A source
that supports it implements :meth:`CatalogSource.write_rows`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ..errors import CatalogError

MODES = ("append", "overwrite", "merge")
WHEN_MATCHED = ("update", "delete", "ignore")
WHEN_NOT_MATCHED = ("insert", "ignore")

#: Name of the session-scoped table a write is first loaded into.
LOAD_TABLE = "nova_writeback_load"


class WriteRefused(CatalogError):
    """The source refused the write, or the request does not fit the table."""


@dataclass(frozen=True)
class WriteRequest:
    schema: str
    table: str
    columns: tuple[str, ...]
    rows: Sequence[Sequence[Any]]
    mode: str = "append"
    #: ``merge``: the columns that identify a row in the target.
    keys: tuple[str, ...] = ()
    when_matched: str = "update"
    when_not_matched: str = "insert"
    timeout_seconds: int = 300

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise WriteRefused(f"mode must be one of {', '.join(MODES)}")
        if self.when_matched not in WHEN_MATCHED:
            raise WriteRefused(f"when_matched must be one of {', '.join(WHEN_MATCHED)}")
        if self.when_not_matched not in WHEN_NOT_MATCHED:
            raise WriteRefused(
                f"when_not_matched must be one of {', '.join(WHEN_NOT_MATCHED)}"
            )
        if not self.columns:
            raise WriteRefused("The data has no columns")
        if len(set(map(str.lower, self.columns))) != len(self.columns):
            raise WriteRefused("The data has duplicate column names")
        if self.mode == "merge":
            if not self.keys:
                raise WriteRefused("merge_into needs on=[...] key columns")
            lowered = {column.lower() for column in self.columns}
            missing = [key for key in self.keys if key.lower() not in lowered]
            if missing:
                raise WriteRefused(f"Key columns not in the data: {', '.join(missing)}")


@dataclass(frozen=True)
class WriteResult:
    inserted: int = 0
    updated: int = 0
    deleted: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


def match_columns(wanted: Sequence[str], existing: Sequence[str]) -> list[str]:
    """The target's spelling of each wanted column (exact, then case-insensitive)."""
    exact = set(existing)
    folded: dict[str, str] = {}
    for name in existing:
        folded.setdefault(name.lower(), name)
    matched: list[str] = []
    missing: list[str] = []
    for name in wanted:
        if name in exact:
            matched.append(name)
        elif name.lower() in folded:
            matched.append(folded[name.lower()])
        else:
            missing.append(name)
    if missing:
        raise WriteRefused("Columns not in the target table: " + ", ".join(missing))
    return matched
