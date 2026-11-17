"""Run every StarRocks 4.1.4 docs SQL example through Nova and, when Nova fails,
directly on the engine as the same user, to find what Nova changes.

Usage (from backend/, NP = nova_admin password in the environment; the corpus
and results live in ~/.local/share/nova/starrocks-compat):
    uv run python scripts/starrocks_compat/probe.py \
        --pages 'sql-reference/sql-functions/*' --out results-functions.jsonl

Outcomes per statement:
  ok           Nova ran it.
  nova_gap     Nova refused or failed, the engine ran it: a Nova compatibility gap.
  both_fail    Both failed (missing fixture data, external system, docs error).
  template     A syntax template with placeholders; not runnable as written.
  skipped      Not run on the shared dev stack (cluster-wide or account changes),
               or it would touch a database that existed before the run.

--fixtures runs fixtures/<page path>.sql (through Nova, in the page's scratch
database) before the page, so examples find the tables and rows the docs assume
(rows `<page>:fixture:<n>`: fixture_ok, fixture_error, fixture_refused). Docs
tables asking for more than one replica get one (the stack has one backend);
such rows carry "adapted": true.

--order-strict settles every "Nova fails, the engine runs it" row: the statement
is re-run from the same rebuilt state (scratch database, fixture, earlier page
statements) on Nova and on the engine. Outcomes then also include
  order_artifact  Both run in isolation: the first result came from probe order.
  timing          Nova ran it on a retry a few seconds later (asynchronous job).
With --keys <json list>, only those statements are settled, each in isolation.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import fnmatch
import hashlib
import json
import os
import re
import time
from pathlib import Path

import asyncmy
import httpx

HOME = Path.home() / ".local/share/nova/starrocks-compat"
NOVA = "http://127.0.0.1:5173"
ENGINE = {"host": "127.0.0.1", "port": int(os.environ.get("ENGINE_PORT", "29030"))}

PROMPT = re.compile(r"^\s*(mysql|StarRocks|starrocks|MySQL)\s*(\[[^\]]*\])?\s*>\s?", re.I)
CONTINUATION = re.compile(r"^\s*->\s?")
OUTPUT_LINE = re.compile(
    r"^\s*([+|]|\d+ rows? in set|Query OK|Empty set|Records:|ERROR \d+|\*{5,}|Warnings?:)", re.I
)
# Placeholders such as <db_name> or <table name>, never SQL types like MAP<VARCHAR(50),INT>.
TEMPLATE = re.compile(
    r"(?<![A-Za-z])<[a-z][a-z0-9_ ]{0,40}>|\{[a-z_ |]+\}|\[\s*[A-Z_ ]+\s*\|"
    r"|(?<!MAP)(?<!ARRAY)(?<!STRUCT)(?<!map)(?<!array)(?<!struct)"
    r"<[A-Za-z_][A-Za-z_ ]{0,40}>(?!\s*\()",
    re.M,
)
ELLIPSIS = re.compile(r"(^|\s)(\.\.\.|…)(\s|$)")

# Not run on the shared development stack: cluster-wide or account changes.
UNSAFE = re.compile(
    r"^\s*(ALTER\s+SYSTEM|ADMIN\b|SET\s+GLOBAL|SET\s+PROPERTY|KILL\b|SHUTDOWN|"
    r"(CREATE|ALTER|DROP)\s+(USER|ROLE|RESOURCE\s+GROUP|STORAGE\s+VOLUME|WAREHOUSE|"
    r"SECURITY\s+INTEGRATION|ROLE\s+MAPPING|GROUP\s+PROVIDER|COMPUTE\s+NODE|BACKEND|FRONTEND)|"
    r"SET\s+(DEFAULT\s+)?ROLE|SET\s+PASSWORD|GRANT\b|REVOKE\b|INSTALL\b|UNINSTALL\b|"
    r"(CANCEL\s+)?DECOMMISSION|SET\s+DEFAULT\s+STORAGE\s+VOLUME|"
    r"(ENABLE|DISABLE)\s+|CLEAN\s+|EXECUTE\s+AS|SET\s+CATALOG|"
    r"DROP\s+(GLOBAL\s+FUNCTION|RESOURCE\b|REPOSITORY|ALL\s+ANALYZE|ANALYZE|SQL\s+BLACKLIST|"
    r"BASELINE)|CREATE\s+ANALYZE\s+ALL|KILL\s+ANALYZE|"
    r"DROP\s+CATALOG|DROP\s+DATABASE\s+(?!IF\s+EXISTS\s+compat_|compat_))",
    re.I,
)
EGRESS = re.compile(
    r"\bINTO\s+(OUTFILE|FILES\s*\()|^\s*EXPORT\b|\bCREATE\s+REPOSITORY\b", re.I | re.M
)
# Writing a VARIANT value into a native table aborts the pinned backend
# ("olap_type_infra.h:161 unknown type 22", 2026-11-16): never run on the shared stack.
ENGINE_CRASH = re.compile(r"^\s*(INSERT|CREATE\s+TABLE|UPDATE)\b[\s\S]*\bVARIANT\b", re.I)
READ_ONLY = re.compile(r"^\s*(SELECT|WITH|SHOW|DESC|DESCRIBE|EXPLAIN|\()", re.I)


def statements(block: str) -> list[str]:
    """Split a docs block into statements: prompts and printed results removed."""
    lines = []
    for raw in block.splitlines():
        line = PROMPT.sub("", raw)
        line = CONTINUATION.sub("", line) if PROMPT.match(raw) is None else line
        if OUTPUT_LINE.match(line):
            continue
        lines.append(line)
    text = "\n".join(lines)
    out, current, quote, i = [], [], None, 0
    while i < len(text):
        char = text[i]
        if quote:
            current.append(char)
            if char == "\\" and quote != "`" and i + 1 < len(text):
                current.append(text[i + 1])
                i += 2
                continue
            if char == quote:
                quote = None
        elif char in "'\"`":
            quote = char
            current.append(char)
        elif text.startswith("--", i) or char == "#":
            end = text.find("\n", i)
            end = len(text) if end < 0 else end
            i = end
            continue
        elif text.startswith("/*", i) and not text.startswith("/*+", i):
            end = text.find("*/", i + 2)
            end = len(text) if end < 0 else end + 2
            i = end
            continue
        elif char == ";":
            out.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        i += 1
    out.append("".join(current).strip())
    return [s for s in out if s and not s.startswith("\\")]


STATEMENT = re.compile(
    r"^\s*(\(|SELECT|WITH|INSERT|CREATE|ALTER|DROP|SHOW|DESC|DESCRIBE|EXPLAIN|SET|USE|LOAD|EXPORT|SUBMIT|"
    r"CANCEL|PAUSE|RESUME|STOP|UPDATE|DELETE|TRUNCATE|REFRESH|ANALYZE|BACKUP|RESTORE|RECOVER|"
    r"BEGIN|START|COMMIT|ROLLBACK|ADD|EXECUTE|PREPARE|DEALLOCATE|CACHE|CLEAR|SYNC|MERGE|"
    r"VALUES|TABLE|GRANT|REVOKE|ADMIN|KILL|INSTALL|UNINSTALL|RETRY|SUBMIT|CALL|LIST|PUT|COPY|REMOVE)\b",
    re.I,
)


def classify(sql: str, existing: set[str], scratch: str) -> str | None:
    if not STATEMENT.match(sql):
        return "signature"
    if TEMPLATE.search(sql) or ELLIPSIS.search(sql):
        return "template"
    if UNSAFE.match(sql):
        return "skipped:cluster_or_account"
    if ENGINE_CRASH.match(sql):
        return "skipped:engine_crash"
    if not READ_ONLY.match(sql):
        names = {token.lower() for token in re.findall(r"`?([A-Za-z_][A-Za-z0-9_]*)`?\s*\.", sql)}
        names |= {
            m.lower()
            for m in re.findall(
                r"\b(?:DATABASE|SCHEMA|USE)\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?`?([A-Za-z_][A-Za-z0-9_]*)",
                sql,
                re.I,
            )
        }
        if names & existing - {scratch.lower()}:
            return "skipped:existing_database"
    return None


async def nova_run(
    client: httpx.AsyncClient, token: str, sql: str, database: str
) -> tuple[bool, str]:
    try:
        response = await client.post(
            "/api/v1/query/execute",
            headers={"Authorization": f"Bearer {token}"},
            json={"sql": sql, "database": database, "max_rows": 20, "confirm_destructive": True},
            timeout=90,
        )
    except httpx.HTTPError as exc:
        return False, f"http:{type(exc).__name__}"
    if response.status_code != 200:
        try:
            payload = response.json()
            detail = payload.get("detail") if isinstance(payload, dict) else payload
        except ValueError:
            detail = response.text
        return False, f"{response.status_code}:{str(detail)[:300]}"
    body = response.json()
    results = body if isinstance(body, list) else [body]
    for item in results:
        if item.get("needs_confirmation"):
            return False, "needs_confirmation"
        if not item.get("success", True):
            return False, f"error:{str(item.get('error'))[:300]}"
    return True, ""


async def engine_run(connection, sql: str, database: str) -> tuple[bool, str]:
    try:
        async with connection.cursor() as cursor:
            # Qualify with the catalog: an earlier docs statement such as
            # `USE hive1.db` would otherwise leave the session in that catalog.
            parts = database.split(".")
            if len(parts) == 1:
                parts = ["default_catalog", *parts]
            await cursor.execute("USE " + ".".join(f"`{part}`" for part in parts))
            await cursor.execute(sql)
            if cursor.description:
                await cursor.fetchmany(20)
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}:{str(exc)[:300]}"


FIXTURES = Path(__file__).resolve().parent / "fixtures"
LEDGER = HOME / "probe-created.json"
CREATE_DB = re.compile(
    r"^\s*CREATE\s+(?:DATABASE|SCHEMA)\s+(?:IF\s+NOT\s+EXISTS\s+)?`?([A-Za-z_][A-Za-z0-9_]*)`?\s*$"
    r"|^\s*CREATE\s+(?:DATABASE|SCHEMA)\s+(?:IF\s+NOT\s+EXISTS\s+)?`?([A-Za-z_][A-Za-z0-9_]*)`?\s",
    re.I,
)
USE = re.compile(r"^\s*USE\s+`?([A-Za-z_][\w.]*)`?\s*$", re.I)
# The development stack has one backend: docs tables that ask for 3 replicas are
# created with 1 when fixtures are on (an environment adaptation, recorded per row).
SINGLE_BE = [
    (re.compile(r"""(["']replication_num["']\s*=\s*["'])[2-9](["'])""", re.I), r"\g<1>1\2"),
    (
        re.compile(r"""(["']replication_allocation["']\s*=\s*["'][^"':]*:\s*)[2-9]""", re.I),
        r"\g<1>1",
    ),
]


def fixture_path(page: str) -> Path:
    return FIXTURES / Path(page).with_suffix(".sql")


def fixture_statements(page: str) -> list[str]:
    path = fixture_path(page)
    return statements(path.read_text()) if path.exists() else []


def created_database(sql: str) -> str | None:
    match = CREATE_DB.match(sql)
    if not match:
        return None
    return (match.group(1) or match.group(2)).lower()


class Nova:
    """Nova's HTTP SQL API as nova_admin; signs in again when the token expires."""

    def __init__(self, client: httpx.AsyncClient, password: str) -> None:
        self.client, self.password, self.token = client, password, ""

    async def login(self) -> None:
        response = await self.client.post(
            "/api/v1/auth/login", json={"username": "nova_admin", "password": self.password}
        )
        self.token = response.json()["access_token"]

    async def run(self, sql: str, database: str) -> tuple[bool, str]:
        ok, error = await nova_run(self.client, self.token, sql, database)
        if not ok and error.startswith("401:"):
            await self.login()
            ok, error = await nova_run(self.client, self.token, sql, database)
        return ok, error


def read_ledger() -> dict[str, int]:
    with contextlib.suppress(FileNotFoundError, ValueError):
        return {str(k): int(v) for k, v in json.loads(LEDGER.read_text()).items()}
    return {}


def write_ledger(ledger: dict[str, int]) -> None:
    LEDGER.write_text(json.dumps(ledger, indent=1, sort_keys=True))


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Databases:
    """Databases this probe created. Only these are ever dropped.

    A ledger file keeps the names until they are dropped, so a run that was
    interrupted (for example when the engine stopped) does not leave a scratch
    database that a later run would mistake for a pre-existing one.
    """

    def __init__(self, existing: set[str]) -> None:
        ledger = read_ledger()
        self.orphans = {name for name, pid in ledger.items() if not pid_alive(pid)}
        self.existing = existing - self.orphans

    def remember(self, name: str) -> None:
        ledger = read_ledger()
        ledger[name.lower()] = os.getpid()
        write_ledger(ledger)

    async def drop(self, engine, names: set[str]) -> None:
        ledger = read_ledger()
        async with engine.cursor() as cursor:
            for name in sorted(names - self.existing):
                try:
                    await cursor.execute(f"DROP DATABASE IF EXISTS `{name}` FORCE")
                except Exception as exc:  # noqa: BLE001
                    # Kept in the ledger: the next run drops it.
                    print(f"could not drop {name}: {type(exc).__name__}", flush=True)
                    continue
                ledger.pop(name, None)
        write_ledger(ledger)


async def engine_connect(password: str):
    connection = await asyncmy.connect(
        user="nova_admin", password=password, autocommit=True, **ENGINE
    )
    async with connection.cursor() as cursor:
        await cursor.execute("SET query_timeout = 120")
    return connection


def page_statements(blocks: list[dict], rewrites) -> list[tuple[str, dict, str, bool]]:
    """(key, block, sql, adapted) for every statement of a page, in docs order."""
    out = []
    for block in blocks:
        for index, original in enumerate(statements(block["text"])):
            sql = original
            for pattern, replacement in rewrites:
                sql = pattern.sub(replacement, sql)
            out.append((f"{block['page']}:{block['line']}:{index}", block, sql, sql != original))
    return out


async def settle(
    nova: Nova,
    password: str,
    databases: Databases,
    scratch: str,
    fixture: list[str],
    prefix: list[str],
    target: str,
) -> dict:
    """Re-run one statement from the same starting state on Nova and on the engine.

    The state is rebuilt twice from nothing: a fresh scratch database, the page
    fixture and every earlier runnable statement of the page, replayed on a new
    engine connection (so neither side inherits session settings or leftovers
    from other statements). Nova runs the target after one rebuild, the engine
    after the other. A Nova failure is retried once after a pause to tell timing
    (an asynchronous job still running) from a real difference.
    """

    async def rebuild():
        connection = await engine_connect(password)
        created = {scratch.lower()}
        await databases.drop(connection, created)
        async with connection.cursor() as cursor:
            await cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{scratch}`")
        databases.remember(scratch)
        current = scratch
        for sql in [*fixture, *prefix]:
            if classify(sql, databases.existing, scratch) or EGRESS.search(sql):
                continue
            made = created_database(sql)
            ok, _ = await engine_run(connection, sql, current)
            if ok and made and made not in databases.existing:
                created.add(made)
                databases.remember(made)
            use = USE.match(sql)
            if ok and use and "." not in use.group(1):
                current = use.group(1)
        return connection, current, created

    result: dict = {}
    connection, current, created = await rebuild()
    try:
        nova_ok, nova_error = await nova.run(target, current)
        if not nova_ok:
            await asyncio.sleep(3)
            retry_ok, _ = await nova.run(target, current)
            result["nova_retry"] = "ok" if retry_ok else "fail"
    finally:
        await databases.drop(connection, created)
        connection.close()
    connection, current, created = await rebuild()
    try:
        engine_ok, engine_error = await engine_run(connection, target, current)
    finally:
        await databases.drop(connection, created)
        connection.close()
    result["nova"] = "ok" if nova_ok else nova_error
    result["engine"] = "ok" if engine_ok else engine_error
    if nova_ok and engine_ok:
        result["verdict"] = "order_artifact"
    elif not nova_ok and result.get("nova_retry") == "ok":
        result["verdict"] = "timing"
    elif engine_ok:
        result["verdict"] = "nova_gap"
    elif nova_ok:
        result["verdict"] = "nova_only"
    else:
        result["verdict"] = "both_fail"
    return result


STRICT_OUTCOME = {
    "nova_gap": "nova_gap",
    "order_artifact": "order_artifact",
    "timing": "timing",
    "both_fail": "both_fail",
    "nova_only": "ok",
}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pages", default="*", help="Page glob, or @file with one page glob per line"
    )
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--rewrite",
        help="JSON file of [pattern, replacement] regex pairs applied to every statement, "
        "to point examples at a test connection's catalog",
    )
    parser.add_argument(
        "--database",
        help="Run every page in this existing database (catalog.db allowed) instead of a "
        "scratch database; the caller creates and removes it",
    )
    parser.add_argument(
        "--fixtures",
        action="store_true",
        help="Run fixtures/<page>.sql through Nova before each page's statements "
        "(tables and rows the examples assume), and create docs tables with one replica",
    )
    parser.add_argument(
        "--order-strict",
        action="store_true",
        help="Settle every 'Nova fails, engine runs it' result by re-running the "
        "statement in isolation from the same starting state on both sides",
    )
    parser.add_argument(
        "--keys",
        help="JSON list of statement keys: with --order-strict, settle only these "
        "statements, each in isolation, without the normal page run",
    )
    args = parser.parse_args()
    rewrites = (
        [(re.compile(pattern), replacement)
         for pattern, replacement in json.loads(Path(args.rewrite).read_text())]
        if args.rewrite else []
    )
    if args.fixtures:
        rewrites = [*rewrites, *SINGLE_BE]
    if args.keys and not args.order_strict:
        parser.error("--keys needs --order-strict")
    if args.order_strict and args.database:
        parser.error("--order-strict rebuilds scratch databases; it cannot use --database")
    targets = set(json.loads(Path(args.keys).read_text())) if args.keys else None
    rows = [json.loads(line) for line in (HOME / "corpus.jsonl").read_text().splitlines() if line]
    globs = (
        Path(args.pages[1:]).read_text().split() if args.pages.startswith("@") else [args.pages]
    )
    rows = [row for row in rows if any(fnmatch.fnmatch(row["page"], glob) for glob in globs)]
    if targets is not None:
        wanted = {key.rsplit(":", 2)[0] for key in targets}
        rows = [row for row in rows if row["page"] in wanted]
    out = HOME / args.out
    done = set()
    if out.exists():
        done = {json.loads(line)["key"] for line in out.read_text().splitlines() if line}
    password = os.environ["NP"]
    async with httpx.AsyncClient(base_url=NOVA, timeout=90) as client:
        nova = Nova(client, password)
        await nova.login()
        engine = await engine_connect(password)
        async with engine.cursor() as cursor:
            await cursor.execute("SHOW DATABASES")
            databases = Databases({row[0].lower() for row in await cursor.fetchall()})
        await databases.drop(engine, databases.orphans)
        pages: dict[str, list[dict]] = {}
        for row in rows:
            pages.setdefault(row["page"], []).append(row)
        count = 0
        with out.open("a") as sink:

            def emit(record: dict) -> None:
                sink.write(json.dumps(record) + "\n")
                sink.flush()

            for page, blocks in pages.items():
                scratch = "compat_" + hashlib.sha1(page.encode()).hexdigest()[:10]
                fixture = fixture_statements(page) if args.fixtures else []
                listed = page_statements(blocks, rewrites)
                if targets is not None:
                    runnable = [
                        sql for _, _, sql, _ in listed
                        if not classify(sql, databases.existing, scratch)
                    ]
                    for key, block, sql, adapted in listed:
                        if key not in targets or key in done:
                            continue
                        position = next(i for i, item in enumerate(listed) if item[0] == key)
                        prefix = [
                            item[2] for item in listed[:position] if item[2] in runnable
                        ]
                        strict = await settle(
                            nova, password, databases, scratch, fixture, prefix, sql
                        )
                        emit({
                            "key": key, "page": page, "section": block["section"],
                            "sql": sql[:2000], "adapted": adapted, "strict": strict,
                            "outcome": STRICT_OUTCOME[strict["verdict"]],
                        })
                    continue
                if args.database:
                    scratch = args.database
                else:
                    await nova.run(f"CREATE DATABASE IF NOT EXISTS {scratch}", "information_schema")
                    databases.remember(scratch)
                current = scratch
                created = {scratch.lower()}
                pending = [key for key, *_ in listed if key not in done]
                if fixture and pending:
                    for index, sql in enumerate(fixture):
                        key = f"{page}:fixture:{index}"
                        record = {"key": key, "page": page, "section": "fixture", "sql": sql}
                        made = created_database(sql)
                        if classify(sql, databases.existing, scratch) or (
                            made and made in databases.existing
                        ):
                            # A fixture never touches a database that existed before.
                            record["outcome"] = "fixture_refused"
                        else:
                            ok, error = await nova.run(sql, current)
                            record["outcome"] = "fixture_ok" if ok else "fixture_error"
                            if not ok:
                                record["nova_error"] = error
                            if ok and made:
                                created.add(made)
                                databases.remember(made)
                            use = USE.match(sql)
                            if ok and use and "." not in use.group(1):
                                current = use.group(1)
                        if key not in done:
                            emit(record)
                    current = scratch
                for position, (key, block, sql, adapted) in enumerate(listed):
                    if key in done:
                        continue
                    record = {
                        "key": key,
                        "page": page,
                        "section": block["section"],
                        "sql": sql[:2000],
                    }
                    if adapted:
                        record["adapted"] = True
                    verdict = classify(sql, databases.existing, scratch.rpartition(".")[2])
                    if verdict:
                        record["outcome"] = verdict
                    else:
                        use = USE.match(sql)
                        started = time.perf_counter()
                        ok, error = await nova.run(sql, current)
                        record["seconds"] = round(time.perf_counter() - started, 2)
                        made = created_database(sql)
                        if made and made not in databases.existing:
                            created.add(made)
                            databases.remember(made)
                        if ok:
                            record["outcome"] = "ok"
                            if use and "." not in use.group(1):
                                current = use.group(1)
                        elif EGRESS.search(sql):
                            # Never retried on the engine: a raw path would be
                            # written with the engine's own storage identity.
                            record["nova_error"] = error
                            record["outcome"] = "both_fail"
                            record["engine_error"] = "not retried: writes files"
                        else:
                            record["nova_error"] = error
                            # A fresh connection per retry: no session settings or
                            # catalog left behind by an earlier retried statement.
                            retry = await engine_connect(password)
                            try:
                                engine_ok, engine_error = await engine_run(retry, sql, current)
                            finally:
                                retry.close()
                            record["outcome"] = "nova_gap" if engine_ok else "both_fail"
                            if not engine_ok:
                                record["engine_error"] = engine_error
                            elif args.order_strict and not args.database:
                                runnable = [
                                    item[2] for item in listed[:position]
                                    if not classify(item[2], databases.existing, scratch)
                                ]
                                await databases.drop(engine, created)
                                strict = await settle(
                                    nova, password, databases, scratch, fixture, runnable, sql
                                )
                                record["strict"] = strict
                                record["outcome"] = STRICT_OUTCOME[strict["verdict"]]
                                # The page continues from its replayed state.
                                current = await replay_page(
                                    engine, databases, scratch, fixture,
                                    [item[2] for item in listed[: position + 1]], created,
                                )
                    emit(record)
                    count += 1
                    if args.limit and count >= args.limit:
                        return
                # Remove only databases this page created, never one that existed before.
                if not args.database:
                    await databases.drop(engine, created)
        engine.close()


async def replay_page(
    engine, databases: Databases, scratch: str, fixture: list[str], done: list[str],
    created: set[str],
) -> str:
    """Rebuild a page's state on the engine after an isolated re-run reset it."""
    async with engine.cursor() as cursor:
        await cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{scratch}`")
    databases.remember(scratch)
    current = scratch
    for sql in [*fixture, *done]:
        if classify(sql, databases.existing, scratch) or EGRESS.search(sql):
            continue
        ok, _ = await engine_run(engine, sql, current)
        made = created_database(sql)
        if ok and made and made not in databases.existing:
            created.add(made)
            databases.remember(made)
        use = USE.match(sql)
        if ok and use and "." not in use.group(1):
            current = use.group(1)
    return current


if __name__ == "__main__":
    asyncio.run(main())
