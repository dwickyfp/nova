import pytest

from app.modules.query.repository import QueryResult
from app.modules.query_autopilot.evidence import plan_uses_object


@pytest.mark.parametrize("lines,expected", [
    (["0:OlapScanNode", "TABLE: nova_ap_mv"], True),
    (["0:OlapScanNode", "TABLE: `retail`.`nova_ap_mv`"], True),
    (["0:OlapScanNode", "rollup: nova_ap_mv"], True),
    (["0:OlapScanNode", "TABLE: orders", "candidate considered: nova_ap_mv"], False),
    (["0:OlapScanNode", "TABLE: nova_ap_mv_other"], False),
    (["unused materialized view: nova_ap_mv"], False),
])
def test_actual_scan_binding_not_a_nearby_candidate_mention(lines, expected):
    result = QueryResult(columns=["plan"], rows=[[line] for line in lines])
    assert plan_uses_object(result, "nova_ap_mv") is expected
    result.truncated = True
    assert plan_uses_object(result, "nova_ap_mv") is False
