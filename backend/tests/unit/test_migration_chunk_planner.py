"""Unit tests for the migration chunk planner (pure, no engine).

The planner decides how a table is split for a bounded, resumable copy. These
tests pin the strategy preference, the range arithmetic (including the max key
never being dropped), and the degradation ladder when metadata is absent.
"""

from __future__ import annotations

import pytest

from app.modules.migration.chunk_planner import (
    ChunkStrategy,
    KeyColumn,
    choose_key_column,
    plan_limit_chunks,
    plan_partition_chunks,
    plan_single_chunk,
    select_plan,
)

INT_KEY = KeyColumn("id", "int")
BIGINT_KEY = KeyColumn("id", "bigint")
STR_KEY = KeyColumn("name", "varchar")
DATE_KEY = KeyColumn("dt", "date")


class TestChooseKeyColumn:
    def test_prefers_the_explicit_column(self):
        columns = (STR_KEY, INT_KEY)
        assert choose_key_column(columns, prefer="id") is INT_KEY

    def test_explicit_non_rangeable_is_refused_not_replaced(self):
        """Asking for a specific key that cannot range must not silently pick
        another — the caller asked for that column."""
        columns = (INT_KEY, STR_KEY)
        assert choose_key_column(columns, prefer="name") is None

    def test_defaults_to_first_rangeable(self):
        columns = (STR_KEY, INT_KEY, BIGINT_KEY)
        assert choose_key_column(columns) is INT_KEY

    def test_no_rangeable_column(self):
        assert choose_key_column((STR_KEY,)) is None

    def test_date_is_rangeable(self):
        assert choose_key_column((DATE_KEY,)) is DATE_KEY


class TestKeyRange:
    def test_splits_into_expected_bucket_count(self):
        plan = select_plan(
            columns=(INT_KEY,),
            key_low=1,
            key_high=10_000,
            rows=10_000,
            partitions=[],
            rows_per_chunk=1_000,
            max_chunks=256,
        )
        assert plan.strategy is ChunkStrategy.KEY_RANGE
        assert plan.key_column == "id"
        assert plan.chunk_count == 10

    def test_ranges_are_contiguous_and_cover_the_max_key(self):
        plan = select_plan(
            columns=(INT_KEY,),
            key_low=1,
            key_high=100,
            rows=100,
            partitions=[],
            rows_per_chunk=25,
            max_chunks=256,
        )
        chunks = plan.chunks
        # First lower bound is the minimum; last upper bound is max + 1 (exclusive).
        assert chunks[0].lower == 1
        assert chunks[-1].upper == 101
        # Each chunk's upper equals the next chunk's lower — no gap, no overlap.
        for left, right in zip(chunks, chunks[1:], strict=False):
            assert left.upper == right.lower

    def test_max_key_is_not_dropped_single_bucket(self):
        plan = select_plan(
            columns=(INT_KEY,),
            key_low=7,
            key_high=7,
            rows=1,
            partitions=[],
            rows_per_chunk=1_000,
            max_chunks=256,
        )
        assert plan.chunk_count == 1
        assert plan.chunks[0].lower == 7
        assert plan.chunks[0].upper == 8  # exclusive bound includes 7

    def test_identical_keys_cannot_split(self):
        plan = select_plan(
            columns=(INT_KEY,),
            key_low=5,
            key_high=5,
            rows=1_000_000,
            partitions=[],
            rows_per_chunk=1,
            max_chunks=256,
        )
        assert plan.chunk_count == 1

    def test_max_chunks_caps_the_bucket_count(self):
        plan = select_plan(
            columns=(INT_KEY,),
            key_low=1,
            key_high=10_000_000,
            rows=10_000_000,
            partitions=[],
            rows_per_chunk=1_000,
            max_chunks=8,
        )
        assert plan.chunk_count == 8

    def test_chunk_bounds_are_literals_safe_to_emit(self):
        from app.modules.migration.data_mover import chunk_predicate

        plan = select_plan(
            columns=(DATE_KEY,),
            key_low="2026-01-01",
            key_high="2026-12-31",
            rows=365,
            partitions=[],
            rows_per_chunk=100,
            max_chunks=256,
        )
        # The planner only range-splits numeric bounds; a string date falls back.
        assert plan.strategy in (ChunkStrategy.KEY_RANGE, ChunkStrategy.LIMIT_PAGING)
        if plan.strategy is ChunkStrategy.KEY_RANGE:
            predicate = chunk_predicate(plan.chunks[0], plan.key_column)
            assert "`dt` >=" in predicate


class TestPartitions:
    def test_one_chunk_per_partition(self):
        plan = select_plan(
            columns=(),
            key_low=None,
            key_high=None,
            rows=1_000_000,
            partitions=["p202601", "p202602", "p202603"],
            rows_per_chunk=1000,
            max_chunks=256,
        )
        assert plan.strategy is ChunkStrategy.PARTITION
        assert [c.partition for c in plan.chunks] == ["p202601", "p202602", "p202603"]

    def test_partition_labels_are_distinct(self):
        plan = plan_partition_chunks(partitions=["a", "b"], rows=10, rows_per_chunk=5)
        labels = [c.label for c in plan.chunks]
        assert len(set(labels)) == len(labels)


class TestLimitPaging:
    def test_pages_cover_the_row_estimate(self):
        plan = plan_limit_chunks(rows=10_000, rows_per_chunk=1_000, max_chunks=256)
        assert plan.strategy is ChunkStrategy.LIMIT_PAGING
        assert plan.chunk_count == 10
        assert plan.chunks[0].offset == 0
        # Last page reaches (at least) the final row.
        last = plan.chunks[-1]
        assert last.offset + last.limit >= 10_000

    def test_page_count_is_capped_and_pages_widen(self):
        plan = plan_limit_chunks(rows=1_000_000, rows_per_chunk=1, max_chunks=10)
        assert plan.chunk_count == 10
        assert plan.chunks[0].limit == 100_000  # widened to stay within the cap

    def test_zero_rows_single_chunk(self):
        plan = plan_limit_chunks(rows=0, rows_per_chunk=1_000, max_chunks=256)
        assert plan.strategy is ChunkStrategy.SINGLE


class TestStrategyPreference:
    def test_key_range_beats_partitions(self):
        plan = select_plan(
            columns=(INT_KEY,),
            key_low=1,
            key_high=100,
            rows=100,
            partitions=["p1", "p2"],
            rows_per_chunk=10,
            max_chunks=256,
        )
        assert plan.strategy is ChunkStrategy.KEY_RANGE

    def test_partitions_beat_paging(self):
        plan = select_plan(
            columns=(),
            key_low=None,
            key_high=None,
            rows=100,
            partitions=["p1", "p2"],
            rows_per_chunk=10,
            max_chunks=256,
        )
        assert plan.strategy is ChunkStrategy.PARTITION

    def test_paging_when_only_rows_known(self):
        plan = select_plan(
            columns=(STR_KEY,),
            key_low=None,
            key_high=None,
            rows=100,
            partitions=[],
            rows_per_chunk=10,
            max_chunks=256,
        )
        assert plan.strategy is ChunkStrategy.LIMIT_PAGING

    def test_single_when_nothing_known(self):
        plan = select_plan(
            columns=(),
            key_low=None,
            key_high=None,
            rows=None,
            partitions=[],
            rows_per_chunk=10,
            max_chunks=256,
        )
        assert plan.strategy is ChunkStrategy.SINGLE

    def test_chunking_disabled_is_single(self):
        plan = select_plan(
            columns=(INT_KEY,),
            key_low=1,
            key_high=100,
            rows=100,
            partitions=["p1"],
            rows_per_chunk=10,
            max_chunks=256,
            chunked=False,
        )
        assert plan.strategy is ChunkStrategy.SINGLE


class TestSinglePlan:
    def test_single_chunk_has_no_bound(self):
        plan = plan_single_chunk()
        assert plan.chunk_count == 1
        assert plan.chunks[0].lower is None
        assert plan.chunks[0].upper is None


@pytest.mark.parametrize("rows_per_chunk,max_chunks", [(0, 256), (-1, 256), (1000, 0)])
def test_degenerate_sizes_do_not_crash(rows_per_chunk, max_chunks):
    plan = plan_limit_chunks(rows=100, rows_per_chunk=rows_per_chunk, max_chunks=max_chunks)
    assert plan.chunk_count >= 1
