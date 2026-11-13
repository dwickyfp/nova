"""Inventory source metadata references without importing application modules.

Live discovery uses only SHOW/SELECT and never starts services or initializes
schemas. Counts describe the inspection interval, not a migration snapshot.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import csv
import hashlib
import json
import re
import subprocess
import warnings
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
QUALIFIED = re.compile(r"\b`?NOVA_SYSTEM`?\s*\.\s*`?([A-Z][A-Z0-9_]+)`?", re.I)
CREATE = re.compile(
    r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?:`?NOVA_SYSTEM`?\s*\.\s*)?`?([A-Z][A-Z0-9_]+)`?\s*\(",
    re.I,
)


def names(sql: str) -> set[str]:
    return {m.upper() for m in QUALIFIED.findall(sql) if not m.endswith("_")}


def string_template(node: ast.AST, bindings: dict[str, str] | None = None) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            string_template(v, bindings)
            if isinstance(v, ast.Constant)
            else (bindings or {}).get(v.value.id, "{dynamic}")
            if isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name)
            else "{dynamic}"
            for v in node.values
        )
    if isinstance(node, ast.Name):
        return (bindings or {}).get(node.id, "")
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return string_template(node.left, bindings) + string_template(node.right, bindings)
    return ""


def literal_bindings(tree: ast.Module) -> dict[str, str]:
    bindings = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = string_template(node.value, bindings)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
            bindings[node.target.id] = string_template(node.value, bindings)
    return bindings


def docstrings(tree: ast.Module) -> set[int]:
    return {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }


def adapter_bindings(
    tree: ast.Module, parents: dict[int, ast.AST]
) -> dict[tuple[int, str], set[str]]:
    """Find possible local aliases, injected defaults and SQL pass-throughs.

    These are reachability candidates, not proof of which branch executes or
    whether its SQL is metadata. Scope keys keep unrelated classes separate.
    """
    bindings: dict[tuple[int, str], set[str]] = defaultdict(set)

    def owner(node: ast.AST, *, class_scope: bool = False) -> int:
        while id(node) in parents:
            node = parents[id(node)]
            kinds = (ast.ClassDef,) if class_scope else (ast.FunctionDef, ast.AsyncFunctionDef)
            if isinstance(node, kinds):
                return id(node)
        return id(tree)

    def key(expression: ast.AST, location: ast.AST) -> tuple[int, str]:
        class_scope = (
            isinstance(expression, ast.Attribute)
            and isinstance(expression.value, ast.Name)
            and expression.value.id in {"self", "cls"}
        )
        return owner(location, class_scope=class_scope), ast.unparse(expression)

    def origins(expression: ast.AST, location: ast.AST) -> set[str]:
        if isinstance(expression, ast.Attribute) and expression.attr in {
            "execute_system", "system_conn"
        }:
            return {expression.attr}
        if isinstance(expression, (ast.Name, ast.Attribute)):
            return (
                bindings[key(expression, location)]
                | bindings[(id(tree), ast.unparse(expression))]
            )
        if isinstance(expression, ast.BoolOp):
            return set().union(*(origins(value, location) for value in expression.values))
        if isinstance(expression, ast.IfExp):
            return origins(expression.body, location) | origins(expression.orelse, location)
        return set()

    assignments = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            assignments.extend((target, node.value, node) for target in node.targets)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            assignments.append((node.target, node.value, node))
        elif isinstance(node, ast.Return):
            call = node.value.value if isinstance(node.value, ast.Await) else node.value
            if not isinstance(call, ast.Call) or not call.args:
                continue
            function = parents.get(id(node))
            while function is not None and not isinstance(
                function, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                function = parents.get(id(function))
            if function is None:
                continue
            arguments = [arg.arg for arg in function.args.args if arg.arg not in {"self", "cls"}]
            if not arguments or not isinstance(call.args[0], ast.Name):
                continue
            if call.args[0].id != arguments[0]:
                continue
            target = ast.parse(f"self.{function.name}", mode="eval").body
            assignments.append((target, call.func, node))
    while True:
        previous = sum(len(values) for values in bindings.values())
        for target, value, location in assignments:
            bindings[key(target, location)].update(origins(value, location))
        if sum(len(values) for values in bindings.values()) == previous:
            break
    return {
        (id(node), ast.unparse(node.func)): origins(node.func, node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and origins(node.func, node)
    }


def inspect_python(source: str, path: str) -> tuple[dict[str, set[str]], list[dict]]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(source)
    references: dict[str, set[str]] = defaultdict(set)
    calls = []
    # JoinedStr already includes its constant children; count each SQL template once.
    bindings = literal_bindings(tree)
    parents = {
        id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
    }
    adapters = adapter_bindings(tree, parents)
    children = docstrings(tree) | {
        id(c) for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) for c in n.values
    }
    function_tables: dict[int, set[str]] = {}
    for node in ast.walk(tree):
        if id(node) not in children:
            for name in names(string_template(node, bindings)):
                references[name].add(f"{path}:{node.lineno}")
        if not isinstance(node, ast.Call):
            continue
        origins = adapters.get((id(node), ast.unparse(node.func)))
        potential_cursor = (
            isinstance(node.func, ast.Attribute) and node.func.attr in {"execute", "executemany"}
        )
        potential_connection = (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in {"connect", "create_pool"}
            and ast.unparse(node.func.value) in {"asyncmy", "aiomysql", "pymysql"}
        )
        if not origins and not potential_cursor and not potential_connection:
            continue
        scope = node
        containers = []
        function = None
        while id(scope) in parents:
            scope = parents[id(scope)]
            if isinstance(scope, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                containers.append(scope.name)
                if function is None and isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    function = scope
        query = string_template(node.args[0], bindings) if node.args else ""
        first_word = re.match(r"\s*([A-Za-z]+)\b", query)
        kind = first_word.group(1).upper() if first_word else "DYNAMIC"
        candidate_scope = function or node
        if id(candidate_scope) not in function_tables:
            candidates = set()
            for expression in ast.walk(candidate_scope):
                if id(expression) not in children:
                    candidates.update(names(string_template(expression, bindings)))
            function_tables[id(candidate_scope)] = candidates
        candidates = function_tables[id(candidate_scope)]
        dispatch = ""
        if not origins and isinstance(node.func, ast.Attribute):
            if potential_cursor and (candidates or "NOVA_SYSTEM" in query.upper()):
                origins = {node.func.attr}
                dispatch = "metadata_cursor_candidate"
            elif potential_connection:
                origins = {node.func.attr}
                dispatch = "connection_candidate"
        if not origins:
            continue
        direct = isinstance(node.func, ast.Attribute) and node.func.attr in origins
        calls.append(
            {
                "path": path,
                "line": node.lineno,
                "column": node.col_offset,
                "end_line": node.end_lineno,
                "end_column": node.end_col_offset,
                "api": ";".join(sorted(origins)),
                "dispatch": dispatch or ("direct" if direct else "indirect_candidate"),
                "receiver": ast.unparse(node.func.value) if direct else ast.unparse(node.func),
                "function": ".".join(reversed(containers)),
                "statement_kind": kind,
                "literal_tables": sorted(names(query)),
                "function_candidate_tables": sorted(candidates),
                "source_file_sha256": hashlib.sha256(source.encode()).hexdigest(),
                "callsite_sha256": hashlib.sha256(ast.dump(node).encode()).hexdigest(),
                "review_status": "REQUIRES_CLASSIFICATION",
                "review_notes": "",
            }
        )
    return dict(references), calls


def scan(root: Path) -> dict[str, Any]:
    references: dict[str, set[str]] = defaultdict(set)
    declarations: dict[str, set[str]] = defaultdict(set)
    calls = []
    paths = sorted((root / "backend/app").rglob("*.py"))
    tree_digest = hashlib.sha256()
    for file in paths:
        relative = str(file.relative_to(root))
        source = file.read_text()
        tree_digest.update(relative.encode() + b"\0" + source.encode() + b"\0")
        found, file_calls = inspect_python(source, relative)
        calls.extend(file_calls)
        for name, locations in found.items():
            references[name].update(locations)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(source)
        bindings = literal_bindings(tree)
        ignored = docstrings(tree)
        for node in ast.walk(tree):
            if id(node) in ignored:
                continue
            for name in CREATE.findall(string_template(node, bindings)):
                # Unqualified names count only if metadata references establish ownership.
                declarations[name.upper()].add(f"{relative}:{node.lineno}")
    for file in [
        root / "docker/init-nova.sql",
        *sorted((root / "backend/migrations").glob("*.sql")),
    ]:
        relative = str(file.relative_to(root))
        source = re.sub(r"--[^\n]*", "", file.read_text())
        for match in QUALIFIED.finditer(source):
            name = match.group(1).upper()
            if not name.endswith("_"):
                references[name].add(f"{relative}:{source[: match.start()].count(chr(10)) + 1}")
        for match in CREATE.finditer(source):
            declarations[match.group(1).upper()].add(
                f"{relative}:{source[: match.start()].count(chr(10)) + 1}"
            )
    return {
        "python_files": len(paths),
        "application_tree_sha256": tree_digest.hexdigest(),
        "references": {k: sorted(v) for k, v in sorted(references.items())},
        "declarations": {k: sorted(v) for k, v in sorted(declarations.items()) if k in references},
        "system_calls": sorted(calls, key=lambda c: (c["path"], c["line"])),
    }


def feature_inventory(root: Path) -> list[dict[str, str]]:
    rows = []
    for page in sorted((root / "frontend/src/features/docs/content").rglob("*.md")):
        if page.name == "AUTHORING.md":
            continue
        title = re.search(r"^title:\s*(.+)$", page.read_text(), re.M)
        rows.append(
            {
                "feature": str(page.relative_to(root / "frontend/src/features/docs/content")),
                "title": title.group(1).strip() if title else page.stem,
                "source": str(page.relative_to(root)),
                "happy_path": "NOT_RUN",
                "invalid_input": "NOT_RUN",
                "authorization": "NOT_RUN",
                "dependency_failure": "NOT_RUN",
                "evidence": "",
            }
        )
    return rows


def ui_inventory(root: Path) -> list[dict[str, str]]:
    routes = []
    for source in sorted((root / "frontend/src/routes").rglob("*.tsx")):
        declaration = re.search(r"createFileRoute\(\s*['\"]([^'\"]+)['\"]", source.read_text())
        if declaration:
            routes.append({"path": declaration.group(1), "source": str(source.relative_to(root))})
    return routes


def api_inventory(base_url: str) -> list[dict[str, str]]:
    import httpx

    response = httpx.get(base_url.rstrip("/") + "/openapi.json", timeout=30, follow_redirects=False)
    response.raise_for_status()
    paths = response.json()["paths"]
    operations = []
    for path, methods in sorted(paths.items()):
        for method, operation in sorted(methods.items()):
            if method.lower() not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                continue
            operations.append(
                {
                    "method": method.upper(),
                    "path": path,
                    "operation_id": operation.get("operationId", ""),
                    "summary": operation.get("summary", ""),
                }
            )
    return operations


def merge_feature_inventory(path: Path, new_rows: list[dict[str, str]]) -> None:
    previous = []
    if path.exists():
        with path.open(newline="") as source:
            previous = list(csv.DictReader(source))
    known = {row["feature"] for row in previous}
    # A removed feature also needs acceptance evidence; keep its row for review.
    rows = [*previous, *(row for row in new_rows if row["feature"] not in known)]
    if not rows:
        raise ValueError("Feature inventory is empty")
    with path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


async def inspect_live(host: str, port: int) -> dict[str, Any]:
    import asyncmy

    from app.core.config import settings

    started = datetime.now(UTC).isoformat()
    conn = await asyncmy.connect(
        host=host,
        port=port,
        user=settings.STARROCKS_ROOT_USER,
        password=settings.STARROCKS_ROOT_PASSWORD,
        connect_timeout=5,
        read_timeout=30,
    )
    tables = {}
    try:
        async with conn.cursor() as cur:
            await cur.execute("SELECT VERSION(), @@session.time_zone, @@global.time_zone")
            version, session_timezone, global_timezone = await cur.fetchone()
            await cur.execute("SHOW TABLES FROM NOVA_SYSTEM")
            table_names = sorted(row[0] for row in await cur.fetchall())
            for table in table_names:
                if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", table):
                    raise ValueError("Noncanonical metadata identifier requires manual inventory")
                await cur.execute(f"SHOW CREATE TABLE NOVA_SYSTEM.`{table}`")
                ddl = (await cur.fetchone())[1]
                await cur.execute(f"SELECT COUNT(*) FROM NOVA_SYSTEM.`{table}`")
                count = (await cur.fetchone())[0]
                await cur.execute(
                    "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, ORDINAL_POSITION "
                    "FROM information_schema.columns WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
                    "ORDER BY ORDINAL_POSITION",
                    ("NOVA_SYSTEM", table),
                )
                columns = [
                    dict(zip(("name", "type", "nullable", "ordinal"), row, strict=True))
                    for row in await cur.fetchall()
                ]
                key = re.search(
                    r"(PRIMARY|UNIQUE|DUPLICATE|AGGREGATE)\s+KEY\s*\(([^)]+)\)", ddl, re.I
                )
                tables[table] = {
                    "rows": count,
                    "columns": columns,
                    "key_model": key.group(1).upper() if key else None,
                    "key_columns": [s.strip().strip("`") for s in key.group(2).split(",")]
                    if key
                    else [],
                    "ddl_sha256": hashlib.sha256(ddl.encode()).hexdigest(),
                }
    finally:
        conn.close()
    return {
        "started_at": started,
        "finished_at": datetime.now(UTC).isoformat(),
        "engine_version": version,
        "session_timezone": session_timezone,
        "global_timezone": global_timezone,
        "consistent_snapshot": False,
        "tables": tables,
    }


_CALL_POSITIONS = frozenset({"column", "end_line", "end_column"})
_CALL_REVIEW_FIELDS = frozenset({"review_status", "review_notes"})


def _call_source_binding(row: dict[str, Any], *, legacy: bool = False) -> tuple:
    ignored = _CALL_REVIEW_FIELDS | (_CALL_POSITIONS if legacy else frozenset())

    def serialized(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, list):
            return ";".join(str(item) for item in value)
        return str(value)

    return tuple(
        (key, serialized(value)) for key, value in sorted(row.items()) if key not in ignored
    )


def preserve_call_reviews(current: list[dict], previous: list[dict]) -> None:
    """Carry reviews only across unchanged, unambiguous source locations.

    Older CSVs have line numbers but no columns. Their reviews can be upgraded
    only when both inventories have exactly one call for that complete binding.
    Validate the whole mapping before changing any row or writing the CSV.
    """
    exact: dict[tuple, list[dict]] = defaultdict(list)
    legacy: dict[tuple, list[dict]] = defaultdict(list)
    current_legacy: dict[tuple, list[dict]] = defaultdict(list)
    for row in previous:
        if not all(re.fullmatch(r"[0-9a-f]{64}", str(row.get(key, ""))) for key in (
            "source_file_sha256", "callsite_sha256",
        )):
            continue
        target = exact if row.keys() >= _CALL_POSITIONS else legacy
        target[_call_source_binding(row, legacy=target is legacy)].append(row)
    for row in current:
        current_legacy[_call_source_binding(row, legacy=True)].append(row)
    updates = []
    for row in current:
        matched = exact.get(_call_source_binding(row), [])
        if not matched:
            key = _call_source_binding(row, legacy=True)
            matched = legacy.get(key, [])
            if matched and len(current_legacy[key]) != 1:
                raise ValueError("Ambiguous legacy call review; preserve the inventory for review")
        if len(matched) > 1:
            raise ValueError("Duplicate source-bound call reviews require reconciliation")
        if matched:
            updates.append((row, matched[0]))
    for row, review in updates:
        row["review_status"] = review["review_status"]
        row["review_notes"] = review.get("review_notes", "")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--live-host", help="Explicit existing StarRocks endpoint; read-only discovery"
    )
    parser.add_argument("--live-port", type=int, default=9030)
    parser.add_argument(
        "--live-api", help="Existing Nova API base URL, for read-only OpenAPI inventory"
    )
    args = parser.parse_args()
    result = scan(ROOT)
    result["auxiliary_sources"] = {}
    for directory in ("backend/scripts", "backend/tests", "docker"):
        for file in sorted((ROOT / directory).rglob("*.py")):
            if file.resolve() == Path(__file__).resolve():
                continue
            relative = str(file.relative_to(ROOT))
            refs, calls = inspect_python(file.read_text(), relative)
            if refs or calls:
                result["auxiliary_sources"][relative] = {
                    "references": {k: sorted(v) for k, v in refs.items()},
                    "system_calls": calls,
                }
    result["source_commit"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
    ).strip()
    if args.live_host:
        result["live"] = asyncio.run(inspect_live(args.live_host, args.live_port))
    result["ui_routes"] = ui_inventory(ROOT)
    if args.live_api:
        result["api_operations"] = api_inventory(args.live_api)
    args.output.mkdir(parents=True, exist_ok=True)
    calls_path = args.output / "SYSTEM_CALLS.csv"
    reviews = []
    if calls_path.exists():
        with calls_path.open(newline="") as existing:
            reviews = list(csv.DictReader(existing))
    preserve_call_reviews(result["system_calls"], reviews)
    with calls_path.open("w", newline="") as file:
        writer = csv.DictWriter(
            file, fieldnames=list(result["system_calls"][0]), lineterminator="\n"
        )
        writer.writeheader()
        for row in result["system_calls"]:
            writer.writerow(
                {
                    **row,
                    "literal_tables": ";".join(row["literal_tables"]),
                    "function_candidate_tables": ";".join(row["function_candidate_tables"]),
                }
            )
    (args.output / "inventory.json").write_text(json.dumps(result, indent=2) + "\n")
    features = feature_inventory(ROOT)
    for route in result["ui_routes"]:
        features.append(
            {
                "feature": "ui:" + route["path"],
                "title": route["path"],
                "source": route["source"],
                "happy_path": "NOT_RUN",
                "invalid_input": "NOT_RUN",
                "authorization": "NOT_RUN",
                "dependency_failure": "NOT_RUN",
                "evidence": "",
            }
        )
    for operation in result.get("api_operations", []):
        features.append(
            {
                "feature": f"api:{operation['method']} {operation['path']}",
                "title": operation["summary"],
                "source": operation["operation_id"],
                "happy_path": "NOT_RUN",
                "invalid_input": "NOT_RUN",
                "authorization": "NOT_RUN",
                "dependency_failure": "NOT_RUN",
                "evidence": "",
            }
        )
    feature_path = args.output / "FEATURE_QA.csv"
    merge_feature_inventory(feature_path, features)
    print(
        json.dumps(
            {
                "source_tables": len(result["references"]),
                "system_calls": len(result["system_calls"]),
                "live_tables": len(result.get("live", {}).get("tables", {})),
                "ui_routes": len(result["ui_routes"]),
                "api_operations": len(result.get("api_operations", [])),
            }
        )
    )


if __name__ == "__main__":
    main()
