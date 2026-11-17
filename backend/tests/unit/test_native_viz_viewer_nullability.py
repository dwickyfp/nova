"""A viewer's row filter widens nullability; nothing else may differ silently."""

from uuid import uuid4

from app.modules.viz.contracts.datasets import DatasetField
from app.modules.viz.dataset_service import _as_saved
from app.sql_frontend.binding.models import BoundColumn


def _field(name: str, physical: str, nullable: bool) -> DatasetField:
    return DatasetField(field_id=str(uuid4()), name=name, source_column=name,
                        logical_type="string", physical_type=physical,
                        semantic_role="dimension", nullable=nullable)


SAVED = (_field("city", "varchar(64)", False), _field("amount", "decimal(18, 2)", False))


def test_a_row_filtered_viewer_keeps_the_saved_nullability():
    seen = (BoundColumn("city", "varchar(64)", 1, True),
            BoundColumn("amount", "decimal(18, 2)", 2, True))
    assert [column.nullable for column in _as_saved(seen, SAVED)] == [False, False]


def test_any_other_difference_is_left_for_the_stale_check():
    amount = BoundColumn("amount", "decimal(18, 2)", 2, True)
    renamed = (BoundColumn("town", "varchar(64)", 1, True), amount)
    retyped = (BoundColumn("city", "varchar(32)", 1, True), amount)
    shorter = (BoundColumn("city", "varchar(64)", 1, True),)
    for seen in (renamed, retyped, shorter):
        assert _as_saved(seen, SAVED) == seen
    assert _as_saved(shorter, None) == shorter
