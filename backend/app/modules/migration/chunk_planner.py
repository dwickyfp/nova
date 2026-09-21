"""Chunk planner — split one table's copy into bounded, resumable passes.

A single ``INSERT INTO FILES(...) SELECT * FROM t`` for a table with hundreds of
millions of rows is a query the source BE has to run end to end: one failure
discards all of the work, the query pressures BE memory/spill, and there is no
resume point. This module turns a table into a list of **chunks**, each of which
is exported, imported, and verified independently.

Three strategies, chosen from what the source exposes, in order of preference:

1. **Key range** — when the table has a numeric/monotonic key column, the table
   is split into ``[lo, hi)`` ranges over that column. Ranges are disjoint and
   ordered, so they are cheap to filter (a range predicate) and easy to resume.
2. **Partition** — when the table is range-partitioned the engine's own
   partitions are the chunk boundaries. This is the most natural split, but a
   partition can still be larger than the row target, so it is only used when it
   does not exceed the chunk ceiling absurdly.
3. **Limit paging** — when no usable key exists, the table is read in
   ``LIMIT n OFFSET k`` pages. This is a fallback: a deep OFFSET re-scans, so the
   planner caps the page count and reports the strategy so the operator knows the
   copy is not range-bounded.

This module is **pure**: it takes metadata that the repository read and returns
chunk descriptors; it opens no connection and builds no execution path. The
``data_mover`` turns a chunk descriptor into SQL; the service executes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

#: Types that can be used as a range key. A range predicate needs an orderable
#: value; strings are excluded because the min/max hex range is not a natural
#: row-count boundary and can split mid-collation.
_RANGE_KEY_TYPES: tuple[str, ...] = (
    "tinyint",
    "smallint",
    "int",
    "integer",
    "bigint",
    "largeint",
    "float",
    "double",
    "decimal",
    "numeric",
    "date",
    "datetime",
    "timestamp",
)


class ChunkStrategy(StrEnum):
    """How a table was split into chunks."""

    KEY_RANGE = "key_range"
    PARTITION = "partition"
    LIMIT_PAGING = "limit_paging"
    SINGLE = "single"


@dataclass(frozen=True)
class KeyColumn:
    """A candidate key column for range chunking."""

    name: str
    data_type: str

    @property
    def is_rangeable(self) -> bool:
        return self.data_type.strip().lower().startswith(_RANGE_KEY_TYPES)


@dataclass(frozen=True)
class TableChunk:
    """One bounded slice of a table's rows.

    Exactly one of the bounds is set, matching ``strategy``:

    * ``KEY_RANGE``  — ``lower`` inclusive, ``upper`` exclusive (``None`` = unbounded).
    * ``PARTITION``  — ``partition`` names the engine partition.
    * ``LIMIT_PAGING`` — ``offset`` / ``limit``.
    * ``SINGLE`` — the whole table (no bound).
    """

    index: int
    strategy: ChunkStrategy
    lower: object | None = None
    upper: object | None = None
    lower_inclusive: bool = True
    upper_inclusive: bool = False
    partition: str | None = None
    offset: int | None = None
    limit: int | None = None

    @property
    def label(self) -> str:
        """A stable, filesystem-safe label used in the stage path."""
        if self.strategy is ChunkStrategy.KEY_RANGE:
            lo = "min" if self.lower is None else str(self.lower)
            hi = "max" if self.upper is None else str(self.upper)
            return f"range_{self.index:04d}_{_safe(lo)}_{_safe(hi)}"
        if self.strategy is ChunkStrategy.PARTITION:
            return f"part_{self.index:04d}_{_safe(self.partition or '')}"
        if self.strategy is ChunkStrategy.LIMIT_PAGING:
            return f"page_{self.index:04d}_{self.offset}_{self.limit}"
        return f"single_{self.index:04d}"


def _safe(value: str) -> str:
    """Reduce a bound to characters that are safe in an object-store path."""
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value)[:48]


@dataclass(frozen=True)
class ChunkPlan:
    """The full split for one table."""

    strategy: ChunkStrategy
    chunks: tuple[TableChunk, ...]
    key_column: str | None = None
    reason: str = ""

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)


def choose_key_column(
    columns: tuple[KeyColumn, ...], *, prefer: str | None = None
) -> KeyColumn | None:
    """Pick the best range key: an explicit preference, else the first rangeable.

    The caller passes the table's key columns (primary/duplicate/aggregate or
    sort key) in engine order, so the first rangeable one is the engine's own
    leading key — the column a range scan is cheapest on.
    """
    if prefer:
        for column in columns:
            if column.name == prefer and column.is_rangeable:
                return column
        return None
    for column in columns:
        if column.is_rangeable:
            return column
    return None


def _even_ranges(
    low: int | float,
    high: int | float,
    *,
    rows: int,
    rows_per_chunk: int,
    max_chunks: int,
) -> list[tuple[int | float, int | float]]:
    """Split ``[low, high]`` into disjoint ``[lo, hi)`` ranges.

    The number of buckets is ``ceil(rows / rows_per_chunk)`` clamped to
    ``max_chunks``. The upper bound of the last range is ``high + 1`` (inclusive
    rewritten to exclusive) so the maximum key is never dropped. Integer keys get
    integer-aligned boundaries; float keys are left as-is.
    """
    if rows <= 0 or rows_per_chunk <= 0:
        return [(low, high + 1)]
    desired = -(-rows // rows_per_chunk)  # ceil
    buckets = max(1, min(desired, max_chunks))
    if buckets == 1:
        return [(low, high + 1)]

    span = high - low
    if span <= 0:
        # Every key is identical (or a single value): ranges cannot split it.
        return [(low, high + 1)]

    is_int = isinstance(low, int) and isinstance(high, int)
    # Boundaries are start_0 < start_1 < ... < start_buckets = high + 1, so each
    # chunk is [start_i, start_{i+1}) and the maximum key is never dropped. A
    # boundary derived by integer arithmetic may coincide with the previous one
    # when the span is smaller than the bucket count; those empty chunks are
    # skipped so the ranges stay disjoint.
    boundaries: list[int | float] = []
    for i in range(buckets + 1):
        if i == buckets:
            boundaries.append(high + 1)
        else:
            value = low + (span * i) // buckets
            boundaries.append(int(value) if is_int else value)

    ranges: list[tuple[int | float, int | float]] = []
    for i in range(len(boundaries) - 1):
        start = boundaries[i]
        end = boundaries[i + 1]
        if start >= end:
            continue
        ranges.append((start, end))
    return ranges or [(low, high + 1)]


def plan_key_range_chunks(
    *,
    key: KeyColumn,
    low: int | float,
    high: int | float,
    rows: int,
    rows_per_chunk: int,
    max_chunks: int,
) -> ChunkPlan:
    """Plan chunks over a numeric/date key range ``[low, high]``.

    ``rows`` is the planner's row estimate (from the engine's statistics or a
    count) — only used to size the buckets, never to cut rows.
    """
    ranges = _even_ranges(
        low, high, rows=rows, rows_per_chunk=rows_per_chunk, max_chunks=max_chunks
    )
    chunks = tuple(
        TableChunk(
            index=i,
            strategy=ChunkStrategy.KEY_RANGE,
            lower=lo,
            upper=hi,
            lower_inclusive=True,
            upper_inclusive=False,
        )
        for i, (lo, hi) in enumerate(ranges)
    )
    return ChunkPlan(
        strategy=ChunkStrategy.KEY_RANGE,
        chunks=chunks,
        key_column=key.name,
        reason=f"split on key column '{key.name}' into {len(chunks)} range(s)",
    )


def plan_partition_chunks(*, partitions: list[str], rows: int, rows_per_chunk: int) -> ChunkPlan:
    """Plan chunks from the engine's own range partitions.

    One chunk per partition. A partition larger than the target still works — it
    is simply one bigger pass — so this is always preferred over LIMIT paging
    when partitions exist.
    """
    chunks = tuple(
        TableChunk(index=i, strategy=ChunkStrategy.PARTITION, partition=name)
        for i, name in enumerate(partitions)
    )
    return ChunkPlan(
        strategy=ChunkStrategy.PARTITION,
        chunks=chunks,
        reason=f"one chunk per engine partition ({len(chunks)} partition(s))",
    )


def plan_limit_chunks(*, rows: int, rows_per_chunk: int, max_chunks: int) -> ChunkPlan:
    """Fallback: ``LIMIT n OFFSET k`` pages.

    The page count is capped at ``max_chunks``; a table that would need more is
    read with wider pages (each page larger than ``rows_per_chunk``) rather than
    producing an unbounded number of passes. The reason string records that the
    copy is not range-bounded, so the operator knows a deep OFFSET is in play.
    """
    if rows <= 0 or rows_per_chunk <= 0:
        return ChunkPlan(
            strategy=ChunkStrategy.SINGLE,
            chunks=(TableChunk(index=0, strategy=ChunkStrategy.SINGLE),),
            reason="no row estimate; copying the whole table in one pass",
        )
    pages = -(-rows // rows_per_chunk)
    pages = max(1, min(pages, max_chunks))
    page_size = -(-rows // pages)
    chunks = tuple(
        TableChunk(
            index=i,
            strategy=ChunkStrategy.LIMIT_PAGING,
            offset=i * page_size,
            limit=page_size,
        )
        for i in range(pages)
    )
    return ChunkPlan(
        strategy=ChunkStrategy.LIMIT_PAGING,
        chunks=chunks,
        reason=(
            f"no range key or partition; using {pages} LIMIT/OFFSET page(s) of "
            f"{page_size} rows (not range-bounded)"
        ),
    )


def plan_single_chunk(reason: str = "chunking disabled") -> ChunkPlan:
    """One chunk covering the whole table — the pre-batching behaviour."""
    return ChunkPlan(
        strategy=ChunkStrategy.SINGLE,
        chunks=(TableChunk(index=0, strategy=ChunkStrategy.SINGLE),),
        reason=reason,
    )


def select_plan(
    *,
    columns: tuple[KeyColumn, ...],
    key_low: object | None,
    key_high: object | None,
    rows: int | None,
    partitions: list[str],
    rows_per_chunk: int,
    max_chunks: int,
    chunked: bool = True,
    prefer_key: str | None = None,
) -> ChunkPlan:
    """Choose the chunking strategy from the available source metadata.

    Preference order: key range → partitions → LIMIT paging → single. Any signal
    that is absent falls through to the next. ``chunked=False`` returns a single
    chunk so the caller keeps the single-pass path without a separate code path.
    """
    if not chunked:
        return plan_single_chunk("chunking disabled")
    estimate = rows if rows is not None and rows > 0 else 0

    key = choose_key_column(columns, prefer=prefer_key)
    if (
        key is not None
        and key_low is not None
        and key_high is not None
        and isinstance(key_low, (int, float))
        and isinstance(key_high, (int, float))
    ):
        return plan_key_range_chunks(
            key=key,
            low=key_low,
            high=key_high,
            rows=estimate,
            rows_per_chunk=rows_per_chunk,
            max_chunks=max_chunks,
        )
    if partitions:
        return plan_partition_chunks(
            partitions=partitions, rows=estimate, rows_per_chunk=rows_per_chunk
        )
    if estimate > 0:
        return plan_limit_chunks(
            rows=estimate, rows_per_chunk=rows_per_chunk, max_chunks=max_chunks
        )
    return plan_single_chunk("no key, partition, or row estimate available")
