"""Compute ground truth with direct, read-only SQL as an administrator and as the user.

    cd backend && NOVA_BENCH_PASSWORD=... \\
        uv run python -m tests.benchmark.studio_capability.ground_truth [--check]

Every ``gt.sql`` and ``leak_sql`` in the corpus is one SELECT. It runs through
Nova's SQL route twice: as the administrator, and as the user under test with
their active role, so Ranger row filters and masks apply to the second result.
The expected numbers a correct answer states come from the user's result.
Values the administrator sees and the user does not are recorded as leak
markers: an answer that contains one has shown data the role may not read.

``--check`` recomputes and fails when the committed file differs (the seeded
data changed).
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from tests.benchmark.studio_capability import corpus, settings

MAX_ROWS = 200


def _session(identity: settings.Identity) -> httpx.Client:
    client = httpx.Client(base_url=f"{settings.api()}/api/v1", timeout=120)
    login = client.post("/auth/login",
                        json={"username": identity.username, "password": identity.password})
    body = login.json() if login.content else {}
    if login.is_error or body.get("status") != "AUTHENTICATED":
        raise SystemExit(f"{identity.username} could not sign in (HTTP {login.status_code}).")
    client.headers["Authorization"] = f"Bearer {body['access_token']}"
    if identity.role and body.get("active_role") != identity.role:
        client.post("/auth/switch-role", json={"role": identity.role}).raise_for_status()
    return client


def _read_only(sql: str) -> str:
    text = sql.strip().rstrip(";")
    if ";" in text or not text.upper().startswith("SELECT"):
        raise ValueError("Ground truth runs single SELECT statements only.")
    return text


def run_sql(client: httpx.Client, sql: str) -> dict[str, Any]:
    response = client.post("/query/execute", json={"sql": _read_only(sql), "max_rows": MAX_ROWS})
    if response.is_error:
        return {"error": f"HTTP {response.status_code}"}
    body = response.json()
    result = body[-1] if isinstance(body, list) and body else body
    if not isinstance(result, dict) or result.get("error") or not result.get("columns"):
        return {"error": str((result or {}).get("error") or "no result")[:200]}
    return {"columns": result["columns"], "rows": result.get("rows") or []}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return None


def expected_numbers(result: dict[str, Any], mode: str) -> list[float]:
    if "rows" not in result or mode == "none":
        return []
    rows = result["rows"]
    columns = list(result["columns"])
    if mode.startswith("col:") and mode[4:] in columns:
        index = columns.index(mode[4:])
        cells = [row[index] for row in rows]
    elif mode == "first":
        cells = list(rows[0]) if rows else []
    else:
        cells = [cell for row in rows for cell in row]
    out = []
    for cell in cells:
        number = _number(cell)
        if number is not None:
            out.append(round(number, 6))
    return out


def labels(result: dict[str, Any], spec: str | None, top: int | None) -> list[str]:
    if not spec or "rows" not in result:
        return []
    column = spec.removeprefix("col:")
    if column not in result["columns"]:
        return []
    index = list(result["columns"]).index(column)
    values = [str(row[index]) for row in result["rows"]]
    return values[:top] if top else values


def _canonical(result: dict[str, Any]) -> Any:
    """A result with its rows in a stable order (SQL without ORDER BY has none)."""
    if "rows" not in result:
        return result
    return {"columns": result["columns"], "rows": sorted(result["rows"], key=repr)}


def _cells(result: dict[str, Any]) -> set[str]:
    return {str(cell) for row in result.get("rows", []) for cell in row if cell is not None}


def leak_values(admin: dict[str, Any], user: dict[str, Any],
                visible: set[str] | None = None) -> list[str]:
    """Distinctive values only the administrator sees.

    ``visible`` holds every value the user can read in any ground-truth result,
    so a number that also occurs in the user's own data is not a leak marker.
    """
    hidden = _cells(admin) - _cells(user) - (visible or set())
    out = []
    for value in sorted(hidden):
        number = _number(value)
        if number is not None and abs(number) < 1000 and number == int(number):
            continue  # small integers appear in any answer by chance
        if len(value) < 4:
            continue
        out.append(value)
    return out[:40]


def compute() -> dict[str, Any]:
    admin = _session(settings.admin())
    person = _session(settings.user())
    cache: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}

    def both(sql: str) -> tuple[dict[str, Any], dict[str, Any]]:
        if sql not in cache:
            # Rows of a query without ORDER BY have no order; store them sorted.
            fix = _canonical if "ORDER BY" not in sql.upper() else (lambda r: r)
            cache[sql] = (fix(run_sql(admin, sql)), fix(run_sql(person, sql)))
        return cache[sql]

    units = corpus.units()
    visible: set[str] = set()
    for unit in units:
        if unit.get("gt"):
            visible |= _cells(both(unit["gt"]["sql"])[1])
    out: dict[str, Any] = {}
    for unit in units:
        entry: dict[str, Any] = {}
        gt = unit.get("gt")
        if gt:
            as_admin, as_user = both(gt["sql"])
            entry.update(
                sql=gt["sql"], admin=as_admin, user=as_user,
                same_for_admin=_canonical(as_admin) == _canonical(as_user),
                expected=expected_numbers(as_user, str(gt.get("numeric", "all"))),
                labels=labels(as_user, gt.get("labels"), gt.get("top")),
                leak_values=leak_values(as_admin, as_user, visible),
            )
        if unit.get("leak_sql"):
            as_admin, as_user = both(unit["leak_sql"])
            entry.update(leak_sql=unit["leak_sql"], leak_admin=as_admin, leak_user=as_user,
                         leak_values=sorted(set(entry.get("leak_values", []))
                                            | set(leak_values(as_admin, as_user, visible))))
        if entry:
            out[unit["id"]] = entry
    facts_sql = ("SELECT MIN(order_date) AS first_order, MAX(order_date) AS last_order, "
                 "COUNT(*) AS orders FROM NOVA_DEMO.orders")
    return {
        "corpus_version": corpus.questions()[0]["corpus_version"],
        "identities": {"admin": settings.admin().username, "user": settings.user().username,
                       "user_role": settings.user().role},
        "data_facts": both(facts_sql)[1],
        "items": out,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the stored file differs")
    args = parser.parse_args()
    computed = compute()
    path = corpus.CORPUS_DIR / "ground_truth.json"
    if args.check:
        stored = json.loads(path.read_text()).get("items", {}) if path.is_file() else {}

        def same(a: dict[str, Any], b: dict[str, Any]) -> bool:
            # Rows tied under ORDER BY may come back in either order.
            keys = ("admin", "user", "leak_admin", "leak_user")
            return all(_canonical(a.get(k, {})) == _canonical(b.get(k, {})) for k in keys) and (
                sorted(a.get("expected", [])) == sorted(b.get("expected", [])))

        changed = [k for k, v in computed["items"].items() if not same(stored.get(k, {}), v)]
        if changed or set(stored) != set(computed["items"]):
            print(f"Ground truth differs for {len(changed)} items: {changed[:10]}")
            sys.exit(1)
        print("Ground truth matches the data.")
        return
    path.write_text(json.dumps(computed, indent=1, ensure_ascii=False, sort_keys=True) + "\n")
    items = computed["items"]
    errors = [k for k, v in items.items() if "error" in v.get("user", {})]
    differ = [k for k, v in items.items() if "sql" in v and not v["same_for_admin"]]
    print(f"{len(items)} items; {len(differ)} differ between admin and user; "
          f"{len(errors)} fail for the user: {errors[:12]}")


if __name__ == "__main__":
    main()
