"""The Standard and Append-only change reducers (spec §7).

Input: the change events of one pinned interval, in commit order. Each event
names its row lineage (``row_id``), the committed image before it and after it.
Output: the rows a Stream exposes for that interval, with the Snowflake
metadata columns. Pure and deterministic; no I/O.

Standard, per lineage: ``initial`` is the image before its first event and
``final`` the image after its last one.

- absent → present: ``INSERT(final)``, ``ISUPDATE = FALSE``
- present → absent: ``DELETE(initial)``, ``ISUPDATE = FALSE``
- present → different present: ``DELETE(initial)`` + ``INSERT(final)``, both
  ``ISUPDATE = TRUE``
- otherwise: nothing (net-zero insert/delete, update back to the same values)

A delete and a re-insert of the same key carry different lineages, so they stay
a separate DELETE and INSERT, never an update.

Append-only: one ``INSERT`` per insertion event, with the row as inserted.
Updates and deletes are not emitted, and an inserted row that was deleted
later in the interval is still returned.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from app.modules.streams.schemas import StreamMode

ACTION = "METADATA$ACTION"
IS_UPDATE = "METADATA$ISUPDATE"
ROW_ID = "METADATA$ROW_ID"
METADATA_COLUMNS = (ACTION, IS_UPDATE, ROW_ID)


@dataclass(frozen=True, slots=True)
class ChangeEvent:
    commit_sequence: int
    ordinal: int
    row_id: str
    operation: Literal["INSERT", "UPDATE", "DELETE"]
    before: dict[str, Any] | None
    after: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class StreamRow:
    values: dict[str, Any]
    action: Literal["INSERT", "DELETE"]
    is_update: bool
    row_id: str


def reduce_changes(events: Iterable[ChangeEvent], mode: StreamMode) -> list[StreamRow]:
    ordered = sorted(events, key=lambda event: (event.commit_sequence, event.ordinal))
    if mode == StreamMode.APPEND_ONLY:
        return [
            StreamRow(dict(event.after), "INSERT", False, event.row_id)
            for event in ordered
            if event.before is None and event.after is not None
        ]
    first: dict[str, ChangeEvent] = {}
    last: dict[str, ChangeEvent] = {}
    for event in ordered:
        first.setdefault(event.row_id, event)
        last[event.row_id] = event
    rows: list[StreamRow] = []
    for row_id, opening in first.items():
        initial, final = opening.before, last[row_id].after
        if initial is None and final is None:
            continue
        if initial is None:
            rows.append(StreamRow(dict(final), "INSERT", False, row_id))  # type: ignore[arg-type]
        elif final is None:
            rows.append(StreamRow(dict(initial), "DELETE", False, row_id))
        elif initial != final:
            rows.append(StreamRow(dict(initial), "DELETE", True, row_id))
            rows.append(StreamRow(dict(final), "INSERT", True, row_id))
    return rows
