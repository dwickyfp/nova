import pytest

from app.sql_frontend.autopilot import map_snapshot


@pytest.mark.parametrize("table", ["orders", "retail.orders", "default_catalog.retail.orders"])
@pytest.mark.parametrize(
    "column", ["orders.id", "retail.orders.id", "default_catalog.retail.orders.id"]
)
def test_qualified_columns_follow_snapshot_even_when_table_name_changes(table, column):
    sql = f"SELECT {column} FROM {table}"
    assert map_snapshot(sql, {"orders": "snapshot.saved_orders"}, "retail", "snapshot") == (
        "SELECT `snapshot`.`saved_orders`.`id` FROM `snapshot`.`saved_orders`"
    )


def test_join_aliases_are_preserved_while_registered_relations_change():
    sql = "SELECT a.id,b.id FROM retail.orders a JOIN retail.customers b ON a.customer_id=b.id"
    result = map_snapshot(
        sql,
        {
            "orders": "snapshot.orders_v1",
            "customers": "snapshot.customers_v1",
        },
        "retail",
        "snapshot",
    )
    assert result == (
        "SELECT a.id,b.id FROM `snapshot`.`orders_v1` a JOIN `snapshot`.`customers_v1` b "
        "ON a.customer_id=b.id"
    )


def test_other_database_cannot_use_unqualified_enrollment_by_name():
    with pytest.raises(ValueError, match="table_not_enrolled"):
        map_snapshot(
            "SELECT id FROM private.orders", {"orders": "snapshot.orders"}, "retail", "snapshot"
        )
    result = map_snapshot(
        "SELECT private.orders.id FROM private.orders",
        {"private.orders": "snapshot.orders"},
        "retail",
        "snapshot",
    )
    assert result == "SELECT `snapshot`.`orders`.`id` FROM `snapshot`.`orders`"


def test_ambiguous_scope_and_outside_snapshot_mapping_fail_closed():
    with pytest.raises(ValueError, match="scope_ambiguous"):
        map_snapshot(
            "SELECT orders.id FROM orders WHERE EXISTS(SELECT orders.id FROM orders)",
            {"orders": "snapshot.orders"},
            "retail",
            "snapshot",
        )
    with pytest.raises(ValueError, match="outside_sandbox"):
        map_snapshot("SELECT id FROM orders", {"orders": "production.orders"}, "retail", "snapshot")
