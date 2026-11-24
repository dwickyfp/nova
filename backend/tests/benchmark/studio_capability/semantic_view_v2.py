"""Bring the benchmark's ``demo_sales`` Semantic View to its second version.

    cd backend && NOVA_BENCH_PASSWORD=... \\
        uv run python -m tests.benchmark.studio_capability.semantic_view_v2 [--apply]

The view is created by the benchmark setup in the shared metadata store, owned
by the user under test (``alice``). Version 2 adds what the round-1 re-bench
showed missing (``rebench-rootcause`` S1 and S2):

* ``customer_count`` = ``COUNT(DISTINCT orders.customer_id)``, so "how many
  different customers" has a measure (number-026, typo-006);
* revenue and average order value are rupiah: ``unit`` and ``currency`` IDR,
  instead of ``USD`` and no currency.

Without ``--apply`` (the default) nothing is written: the script reads the
active version as the owner (or, with ``--as-admin``, as the benchmark
administrator under ACCOUNTADMIN, which may manage a public view) and prints
the definition diff. ``--apply`` adds
the edited definition as a **new version** through the owner API, validates
and publishes it; earlier versions stay as they are. It is idempotent: when
the active version already holds both changes it reports that and writes
nothing. Run ``ground_truth.py --check`` afterwards.
"""

from __future__ import annotations

import argparse
import copy
import difflib
import json
import sys
from typing import Any

import httpx

from tests.benchmark.studio_capability import settings
from tests.benchmark.studio_capability.ground_truth import _session

VIEW = "demo_sales"
CURRENCY = "IDR"
#: Metrics stated in rupiah: every one over ``orders.total_amount`` except counts.
MONEY_COLUMN = "orders.total_amount"
CUSTOMER_COUNT: dict[str, Any] = {
    "name": "customer_count",
    "expression": "COUNT(DISTINCT orders.customer_id)",
    "datatype": "Integer",
    "additivity": "non_additive",
    "base_dataset": "orders",
    "default_time_dimension": "order_date",
    "description": "Number of different customers who ordered.",
    "synonyms": ["unique customers", "different customers", "pelanggan unik", "jumlah pelanggan",
                 "orang"],
    "visibility": "public",
}


def edited(definition: dict[str, Any]) -> dict[str, Any]:
    """``definition`` with S1 and S2 applied; unchanged when both are present."""
    result = copy.deepcopy(definition)
    metrics: list[dict[str, Any]] = result.setdefault("metrics", [])
    for metric in metrics:
        expression = str(metric.get("expression") or "")
        if MONEY_COLUMN in expression and "COUNT" not in expression.upper():
            metric["unit"] = CURRENCY
            metric["currency"] = CURRENCY
    if not any(metric.get("name") == CUSTOMER_COUNT["name"] for metric in metrics):
        metrics.append({**_blank_metric(metrics), **copy.deepcopy(CUSTOMER_COUNT),
                        "allowed_dimensions": _order_dimensions(result)})
    return result


def _blank_metric(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    """Every key a stored metric carries, empty: the stored shape stays uniform."""
    if not metrics:
        return {}
    return {key: [] if isinstance(value, list) else {} if isinstance(value, dict) else None
            for key, value in metrics[0].items()}


def _order_dimensions(definition: dict[str, Any]) -> list[str]:
    """What a distinct count may be broken down by: every orders field but the amount.

    A non-additive metric without allowed dimensions refuses every breakdown, so
    "customers by city" needs this list. It matches average_order_value's.
    """
    for metric in definition.get("metrics") or []:
        if metric.get("additivity") == "non_additive" and metric.get("allowed_dimensions"):
            return list(metric["allowed_dimensions"])
    names: list[str] = []
    for dataset in definition.get("datasets") or []:
        if dataset.get("name") != "orders":
            continue
        for item in dataset.get("fields") or []:
            if item.get("name") != MONEY_COLUMN.split(".")[1]:
                names += [item["name"], f"orders.{item['name']}"]
    return sorted(names)


def _diff(before: dict[str, Any], after: dict[str, Any]) -> str:
    old = json.dumps(before, indent=2, sort_keys=True, ensure_ascii=False).splitlines()
    new = json.dumps(after, indent=2, sort_keys=True, ensure_ascii=False).splitlines()
    return "\n".join(difflib.unified_diff(old, new, "active", "v2", lineterm=""))


def _view(client: httpx.Client) -> dict[str, Any]:
    listed = client.get("/semantic-views")
    listed.raise_for_status()
    body = listed.json()
    rows = body.get("items", body) if isinstance(body, dict) else body
    matches = [row for row in rows if str(row.get("name")) == VIEW]
    if len(matches) != 1:
        raise SystemExit(f"Expected one Semantic View named {VIEW}; found {len(matches)}.")
    described = client.get(f"/semantic-views/{matches[0]['id']}")
    described.raise_for_status()
    return described.json()


def _active_definition(view: dict[str, Any]) -> dict[str, Any]:
    active = view.get("active_version")
    for version in view.get("versions") or []:
        if version.get("version") == active:
            return version["definition"]
    raise SystemExit(f"{VIEW} has no active version to start from.")


def _apply(client: httpx.Client, view: dict[str, Any], definition: dict[str, Any]) -> int:
    from app.modules.agents.semantic.ossie import parse_ossie
    from app.modules.agents.semantic.serialize import to_ossie_document

    document = to_ossie_document(definition)
    text = json.dumps(document, ensure_ascii=False)
    # The same parser the server runs: a definition it would refuse is not sent.
    parsed = parse_ossie(text)
    if not parsed.valid:
        raise SystemExit("The edited definition does not parse: " + "; ".join(parsed.errors[:5]))
    created = client.post(f"/semantic-views/{view['id']}/versions", json={"definition": text})
    if created.is_error:
        raise SystemExit(f"Adding the version failed (HTTP {created.status_code}): "
                         f"{created.text[:300]}")
    number = int(created.json()["version"])
    for step in ("validate", "publish"):
        response = client.post(f"/semantic-views/{view['id']}/versions/{number}/{step}")
        if response.is_error:
            raise SystemExit(f"Version {number} was added but {step} failed "
                             f"(HTTP {response.status_code}): {response.text[:300]}")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--apply", action="store_true",
                        help="add, validate and publish the new version (default: dry run)")
    parser.add_argument("--as-admin", action="store_true",
                        help="sign in as the benchmark administrator (ACCOUNTADMIN may manage a "
                             "public view) instead of the owner")
    args = parser.parse_args(argv)
    client = _session(settings.admin() if args.as_admin else settings.user())
    view = _view(client)
    before = _active_definition(view)
    after = edited(before)
    print(f"{VIEW}: active version {view.get('active_version')}")
    if after == before:
        print("The active version already has customer_count and IDR. Nothing to do.")
        return 0
    print(_diff(before, after))
    if not args.apply:
        print("Dry run: nothing written. Run with --apply to add, validate and publish.")
        return 0
    number = _apply(client, view, after)
    published = _active_definition(_view(client))
    if edited(published) != published:
        print(f"Version {number} was published but the active definition still differs.")
        return 1
    print(f"Published {VIEW} version {number}. Now run ground_truth.py --check.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
