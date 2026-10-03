"""Clone enrolled limits for an explicitly selected, disposable resource-group trial."""

from decimal import Decimal, InvalidOperation

from app.modules.resource_groups.schemas import ClassifierSpec
from app.modules.resource_groups.service import build_create_resource_group_sql

_LIMITS = (
    "mem_limit",
    "concurrency_limit",
    "big_query_cpu_second_limit",
    "big_query_scan_rows_limit",
    "big_query_mem_limit",
)


def validate_resource_baseline(production, production_name: str, sandbox, sandbox_name: str):
    from app.modules.query_autopilot.engine_state import resource_group_state

    state = resource_group_state(production, production_name)
    if not state.exists:
        raise ValueError("production_resource_group_unavailable")
    # Identity and classifiers belong to the production binding. The replay
    # selects its sandbox group explicitly, so compare the effective limits.
    before = trial_group_statement(production, production_name, "nova_ap_comparison", {})
    budget = trial_group_statement(sandbox, sandbox_name, "nova_ap_comparison", {})
    if before != budget:
        raise ValueError("sandbox_resource_baseline_differs_from_production")
    return state


def _number(value, *, percent=False) -> Decimal:
    try:
        text = str(value)
        number = Decimal(text.rstrip("%"))
        if percent and text.endswith("%"):
            number /= 100
        if not number.is_finite() or number < 0:
            raise ValueError("sandbox_resource_limit_invalid")
        return number
    except InvalidOperation:
        raise ValueError("sandbox_resource_limit_unavailable") from None


def trial_group_statement(result, source: str, name: str, changes: dict) -> str:
    if result.truncated:
        raise ValueError("resource_group_inventory_truncated")
    rows = [dict(zip(result.columns, row, strict=True)) for row in result.rows]
    matches = [row for row in rows if row.get("name") == source]
    if len(matches) != 1 or set(changes) - set(_LIMITS):
        raise ValueError("sandbox_resource_limits_unsupported")
    current = matches[0]
    if current.get("warehouses") not in {None, ""} or current.get(
        "exclusive_cpu_percent"
    ) not in {None, "null", "0"}:
        raise ValueError("sandbox_resource_isolation_unsupported")
    properties = {}
    for key in _LIMITS:
        original = _number(current.get(key), percent=key == "mem_limit")
        proposed = _number(changes.get(key, original), percent=key == "mem_limit")
        if original > 0 and (proposed == 0 or proposed > original):
            raise ValueError("sandbox_resource_budget_exceeded")
        if key == "mem_limit" and not 0 < proposed <= 1:
            raise ValueError("sandbox_resource_budget_exceeded")
        if key != "mem_limit" and proposed != proposed.to_integral_value():
            raise ValueError("sandbox_resource_limit_invalid")
        properties[key] = str(proposed)
    weight = current.get("cpu_weight_percent")
    if weight in {None, "null"} or not 0 < _number(weight) <= 100:
        raise ValueError("sandbox_resource_cpu_limit_unavailable")
    properties["cpu_weight_percent"] = str(_number(weight))
    spill = current.get("spill_mem_limit_threshold")
    if spill not in {None, "null"}:
        threshold = _number(spill, percent=True)
        if not 0 < threshold <= 1:
            raise ValueError("sandbox_resource_spill_limit_unsupported")
        # SHOW reports 100% for the implicit disabled default, but CREATE only
        # accepts explicit thresholds strictly below one.
        if threshold < 1:
            properties["spill_mem_limit_threshold"] = str(threshold)
    # The pinned engine requires a classifier. No TCP peer can have the
    # unspecified source address; only explicit SET resource_group selects it.
    return build_create_resource_group_sql(
        name, properties, classifiers=(
            ClassifierSpec(user=None, role=None, query_type=None, source_ip="0.0.0.0"),
        ),
    )
