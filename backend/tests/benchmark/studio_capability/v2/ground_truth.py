"""Ground truth of corpus v3: reference queries executed as admin and as the user.

    cd backend && NOVA_BENCH_PASSWORD=... \\
        uv run python -m tests.benchmark.studio_capability.v2.ground_truth --compute [--check]
    cd backend && uv run python -m tests.benchmark.studio_capability.v2.ground_truth --import-v1

``--compute`` runs every reference (primary, alternatives, leak queries) through
Nova's SQL route as the administrator and as the user under test with the
active role, exactly like the v1 runner, and records the data fingerprint and
semantic-model version. ``--check`` recomputes and fails on any difference.
``--import-v1`` builds the file offline from the committed v1 ground truth for
references whose SQL is unchanged; those entries are marked ``imported`` and
the linter blocks a live run until ``--compute`` replaces them. Nothing here
types an expected number: values are cells of executed results.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from tests.benchmark.studio_capability import corpus as corpus_v1
from tests.benchmark.studio_capability.v2 import corpus_v3, provenance
from tests.benchmark.studio_capability.v2.corpus_v3 import COMPUTED, sql_key
from tests.benchmark.studio_capability.v2.expectation import Expectation
from tests.benchmark.studio_capability.v2.grade import References

DATA_FACTS_SQL = ("SELECT MIN(order_date) AS first_order, MAX(order_date) AS last_order, "
                  "COUNT(*) AS orders FROM NOVA_DEMO.orders")


def reference_sqls(unit: Expectation) -> list[str]:
    out: list[str] = []
    if unit.numeric:
        out += [unit.numeric.reference_sql, *unit.numeric.alternatives]
    if unit.governance.leak_sql:
        out.append(unit.governance.leak_sql)
    return out


def fingerprint(result: dict[str, Any]) -> str:
    canonical = json.dumps(result, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def semantic_version(directory: Path = corpus_v3.V3_DIR) -> str | None:
    path = directory / corpus_v3.SEMANTIC_MODEL
    if not path.is_file():
        return None
    model = json.loads(path.read_text())
    return f"{model.get('name')}@{model.get('version')}"


def _leaks(units: list[Expectation], refs: dict[str, Any]) -> dict[str, list[str]]:
    from tests.benchmark.studio_capability.ground_truth import _cells, leak_values

    visible: set[str] = set()  # what the user reads in any answer reference
    for unit in units:
        for sql in ([unit.numeric.reference_sql, *unit.numeric.alternatives]
                    if unit.numeric else []):
            visible |= _cells((refs.get(sql_key(sql)) or {}).get("user") or {})
    out: dict[str, list[str]] = {}
    for unit in units:
        values: set[str] = set()
        for sql in reference_sqls(unit):
            ref = refs.get(sql_key(sql))
            if ref and "admin" in ref:
                values |= set(leak_values(ref["admin"], ref.get("user") or {}, visible))
        if values:
            out[unit.id] = sorted(values)
    return out


def compute(units: list[Expectation]) -> dict[str, Any]:
    """Execute every reference through Nova (no model involved)."""
    from tests.benchmark.studio_capability import ground_truth as v1_gt
    from tests.benchmark.studio_capability import settings

    admin = v1_gt._session(settings.admin())
    person = v1_gt._session(settings.user())
    refs: dict[str, Any] = {}
    for unit in units:
        for sql in reference_sqls(unit):
            key = sql_key(sql)
            if key in refs:
                continue
            fix = v1_gt._canonical if "ORDER BY" not in sql.upper() else (lambda r: r)
            as_admin, as_user = fix(v1_gt.run_sql(admin, sql)), fix(v1_gt.run_sql(person, sql))
            refs[key] = {"sql": sql, "admin": as_admin, "user": as_user,
                         "same_for_admin": v1_gt._canonical(as_admin) == v1_gt._canonical(as_user),
                         "computed_by": COMPUTED}
    facts = v1_gt.run_sql(person, DATA_FACTS_SQL)
    return _document(units, refs, facts, identities={
        "admin": settings.admin().username, "user": settings.user().username,
        "user_role": settings.user().role})


def import_v1(units: list[Expectation]) -> dict[str, Any]:
    """Offline: reuse v1 results whose SQL is unchanged, marked as imported."""
    v1 = corpus_v1.ground_truth()
    by_sql: dict[str, dict[str, Any]] = {}
    for item in (v1.get("items") or {}).values():
        if item.get("sql"):
            by_sql.setdefault(sql_key(item["sql"]), {"admin": item.get("admin"),
                                                      "user": item.get("user")})
        if item.get("leak_sql"):
            by_sql.setdefault(sql_key(item["leak_sql"]), {"admin": item.get("leak_admin"),
                                                           "user": item.get("leak_user")})
    refs: dict[str, Any] = {}
    for unit in units:
        for sql in reference_sqls(unit):
            key = sql_key(sql)
            found = by_sql.get(key)
            if found and found.get("user") is not None and key not in refs:
                refs[key] = {"sql": sql, **found,
                             "same_for_admin": found.get("admin") == found.get("user"),
                             "computed_by": f"imported:v1-{v1.get('corpus_version')}"}
    return _document(units, refs, v1.get("data_facts") or {}, identities=v1.get("identities"))


def _document(units: list[Expectation], refs: dict[str, Any], facts: dict[str, Any], *,
              identities: Any) -> dict[str, Any]:
    pending = sorted({sql_key(s) for u in units for s in reference_sqls(u)} - set(refs))
    return {
        "corpus_version": units[0].corpus_version if units else None,
        "data_facts": facts,
        "data_fingerprint": fingerprint(facts) if facts else None,
        "semantic_model": semantic_version(),
        "identities": identities,
        "references": dict(sorted(refs.items())),
        "pending": pending,
        "leak_values": _leaks(units, refs),
    }


def resolve(unit: Expectation, gt: dict[str, Any],
            by_id: dict[str, Expectation]) -> References:
    """The unit's reference results as the user, rows bound to earlier references."""
    refs = gt.get("references") or {}
    out = References(leak_values=list((gt.get("leak_values") or {}).get(unit.id) or []))
    spec = unit.numeric
    if spec is None:
        return out
    for sql in [spec.reference_sql, *spec.alternatives]:
        ref = refs.get(sql_key(sql))
        result = (ref or {}).get("user")
        if result is None or "rows" not in result:
            out.numeric.append(None)
            continue
        for bind in spec.bind:
            source = by_id.get(bind.source)
            source_ref = refs.get(sql_key(source.numeric.reference_sql)) if (
                source and source.numeric) else None
            source_result = (source_ref or {}).get("user") or {}
            columns = [str(c) for c in source_result.get("columns") or []]
            rows = source_result.get("rows") or []
            if bind.source_column not in columns or len(rows) <= bind.row:
                result = None
                break
            wanted = rows[bind.row][columns.index(bind.source_column)]
            result = provenance.bind_rows(result, bind.column, wanted)
        out.numeric.append(result)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--compute", action="store_true", help="execute through Nova")
    mode.add_argument("--import-v1", action="store_true", help="offline, from the v1 file")
    parser.add_argument("--check", action="store_true", help="with --compute: compare only")
    args = parser.parse_args()
    units = corpus_v3.load_units()
    path = corpus_v3.V3_DIR / corpus_v3.GROUND_TRUTH
    document = compute(units) if args.compute else import_v1(units)
    if args.check:
        stored = corpus_v3.load_ground_truth()
        changed = [k for k, v in document["references"].items()
                   if (stored.get("references") or {}).get(k, {}).get("user") != v["user"]]
        if changed or stored.get("data_fingerprint") != document["data_fingerprint"]:
            print(f"Ground truth differs: {len(changed)} references, fingerprint "
                  f"{stored.get('data_fingerprint')} -> {document['data_fingerprint']}")
            sys.exit(1)
        print("Ground truth matches the data.")
        return
    path.write_text(json.dumps(document, indent=1, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"{len(document['references'])} references, {len(document['pending'])} pending, "
          f"fingerprint {document['data_fingerprint']}")


if __name__ == "__main__":
    main()
