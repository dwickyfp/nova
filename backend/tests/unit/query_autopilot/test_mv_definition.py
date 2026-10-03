import pytest

from app.sql_frontend.autopilot import materialized_view_definition
from app.sql_frontend.parser import parse_statement


def test_mv_retains_unprojected_grouping_key_without_changing_workload_projection():
    sql = (
        "SELECT c.phone,SUM(s.amount) AS total FROM sales s "
        "INNER JOIN customers c ON s.city=c.city GROUP BY s.city,c.phone"
    )
    definition = materialized_view_definition(sql)
    assert definition == sql.replace(
        " AS total FROM", " AS total, s.city AS `nova_ap_group_0` FROM"
    )
    parse_statement(definition)


@pytest.mark.parametrize("group", ["city", "location", "1"])
def test_mv_preserves_projected_group_keys_aliases_and_positions(group):
    sql = f"SELECT city AS location,SUM(amount) AS total FROM sales GROUP BY {group}"
    assert materialized_view_definition(sql) == sql


def test_mv_grouping_alias_collision_and_invalid_position_fail_closed():
    with pytest.raises(ValueError, match="alias_conflict"):
        materialized_view_definition(
            "SELECT SUM(amount) AS nova_ap_group_0 FROM sales GROUP BY city"
        )
    with pytest.raises(ValueError, match="position_unavailable"):
        materialized_view_definition("SELECT SUM(amount) AS total FROM sales GROUP BY 2")


def test_derived_masked_projection_preserves_raw_grouping_dimensions():
    workload = (
        "SELECT LENGTH(c.phone) AS phone_length,SUM(s.amount) AS total "
        "FROM sales s INNER JOIN customers c ON s.city=c.city GROUP BY s.city,c.phone"
    )
    definition = materialized_view_definition(workload)
    assert definition == workload.replace(
        " AS total FROM",
        " AS total, s.city AS `nova_ap_group_0`, c.phone AS `nova_ap_group_1` FROM",
    )
    parse_statement(definition)
