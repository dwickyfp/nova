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
    r"DROP\s+CATALOG|DROP\s+DATABASE\s+(?!IF\s+EXISTS\s+compat_|compat_))",
    re.I,
)
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
            await cursor.execute(f"USE `{database}`")
            await cursor.execute(sql)
            if cursor.description:
                await cursor.fetchmany(20)
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}:{str(exc)[:300]}"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", default="*")
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    rows = [json.loads(line) for line in (HOME / "corpus.jsonl").read_text().splitlines() if line]
    rows = [row for row in rows if fnmatch.fnmatch(row["page"], args.pages)]
    out = HOME / args.out
    done = set()
    if out.exists():
        done = {json.loads(line)["key"] for line in out.read_text().splitlines() if line}
    password = os.environ["NP"]
    async with httpx.AsyncClient(base_url=NOVA) as client:
        login = await client.post(
            "/api/v1/auth/login", json={"username": "nova_admin", "password": password}
        )
        token = login.json()["access_token"]
        engine = await asyncmy.connect(
            user="nova_admin", password=password, autocommit=True, **ENGINE
        )
        async with engine.cursor() as cursor:
            await cursor.execute("SHOW DATABASES")
            existing = {row[0].lower() for row in await cursor.fetchall()}
        pages: dict[str, list[dict]] = {}
        for row in rows:
            pages.setdefault(row["page"], []).append(row)
        count = 0
        with out.open("a") as sink:
            for page, blocks in pages.items():
                scratch = "compat_" + hashlib.sha1(page.encode()).hexdigest()[:10]
                await nova_run(
                    client, token, f"CREATE DATABASE IF NOT EXISTS {scratch}", "information_schema"
                )
                current = scratch
                created = {scratch.lower()}
                for block in blocks:
                    for index, sql in enumerate(statements(block["text"])):
                        key = f"{page}:{block['line']}:{index}"
                        if key in done:
                            continue
                        record = {
                            "key": key,
                            "page": page,
                            "section": block["section"],
                            "sql": sql[:2000],
                        }
                        verdict = classify(sql, existing, scratch)
                        if verdict:
                            record["outcome"] = verdict
                        else:
                            use = re.match(r"^\s*USE\s+`?([A-Za-z_][\w.]*)`?\s*$", sql, re.I)
                            started = time.perf_counter()
                            ok, error = await nova_run(client, token, sql, current)
                            record["seconds"] = round(time.perf_counter() - started, 2)
                            made = re.match(
                                r"^\s*CREATE\s+(?:DATABASE|SCHEMA)\s+(?:IF\s+NOT\s+EXISTS\s+)?`?([A-Za-z_][A-Za-z0-9_]*)",
                                sql,
                                re.I,
                            )
                            if made and made.group(1).lower() not in existing:
                                created.add(made.group(1).lower())
                            if ok:
                                record["outcome"] = "ok"
                                if use and "." not in use.group(1):
                                    current = use.group(1)
                            else:
                                record["nova_error"] = error
                                engine_ok, engine_error = await engine_run(engine, sql, current)
                                record["outcome"] = "nova_gap" if engine_ok else "both_fail"
                                if not engine_ok:
                                    record["engine_error"] = engine_error
                        sink.write(json.dumps(record) + "\n")
                        sink.flush()
                        count += 1
                        if args.limit and count >= args.limit:
                            return
                # Remove only databases this page created, never one that existed before.
                async with engine.cursor() as cursor:
                    for name in sorted(created - existing):
                        with contextlib.suppress(Exception):
                            await cursor.execute(f"DROP DATABASE IF EXISTS `{name}` FORCE")
        engine.close()


if __name__ == "__main__":
    asyncio.run(main())
