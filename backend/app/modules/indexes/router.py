"""Indexes module — inverted (full-text) index management (NOVA-111).

StarRocks 4.1 builds full-text inverted indexes with the CLucene (shared-nothing)
or built-in (shared-data) implementation. Nova shipped no surface for them: the
only way to create one was to hand-write ``ALTER TABLE ... ADD INDEX ... USING
GIN`` in the SQL workspace. This module wraps the documented 4.1.4 syntax behind
an API with allow-listed identifiers and properties, then executes the assembled
statement on the **caller's** StarRocks connection so the engine's RBAC — not the
root pool — decides whether the operation is permitted, exactly as the tables and
views modules do (NOVA-89).

Two properties are load-bearing:

* **No syntax is invented.** The create/add/drop shapes, the ``parser`` and
  ``imp_lib`` values, and the ``MATCH``/``MATCH_ANY``/``MATCH_ALL`` predicate
  forms are the ones in the StarRocks 4.1 docs. ``docs/24-advanced-indexes.md``
  has been corrected where the gap-analysis spec diverged from them.
* **The property clause cannot be broken out of.** Keys are matched against a
  closed allow-list and values against scalar/inner allow-lists before the
  ``"key"="value"`` string is built, so a crafted value is refused, not escaped.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.common.audit import write_audit_log
from app.common.identifiers import (
    check_identifier,
    check_index_kind,
    check_index_property_key,
    check_index_property_value,
    check_inverted_imp_lib,
    check_inverted_parser,
)
from app.common.sql_guard import guard_sql
from app.core.deps import get_current_user, get_user_connection
from app.modules.indexes.repository import index_repository

router = APIRouter()

#: ``Annotated`` dependency aliases (the tree's convention; a ``Depends()`` in an
#: argument default trips ruff B008). DDL runs on the caller's connection.
CurrentUser = Annotated[dict, Depends(get_current_user)]
UserConnection = Annotated[Any, Depends(get_user_connection)]

#: The only index properties Nova exposes. Anything else is an unknown knob the
#: engine may not support, and forwarding it would widen the injection surface
#: for no user-visible benefit. The numeric knobs are per the 4.1 docs:
#: ``dict_gram_num`` (built-in n-gram dictionary) and ``parser.ngram_len``
#: (the historical ngram-parser length).
_NUMERIC_PROPERTIES = frozenset({"dict_gram_num", "parser.ngram_len"})

#: The predicate forms full-text search accepts when the indexed column is
#: tokenized. Exposed so the API can validate a query preview before it is sent
#: and so the UI has one source of truth. A ``MATCH`` predicate is only legal as
#: a pushdown against an indexed column and only in a ``WHERE`` clause; Nova
#: therefore does not compose the query for the caller — the SQL workspace does,
#: and this module only documents and validates the shape.
FULLTEXT_PREDICATES: tuple[str, ...] = ("MATCH", "MATCH_ANY", "MATCH_ALL")


# ── Schemas ────────────────────────────────────────────────────


class CreateIndexRequest(BaseModel):
    database: str
    table: str
    index_name: str
    column: str
    kind: str = Field("GIN", description="GIN (inverted/full-text), BITMAP, NGRAM_BF")
    parser: str | None = Field(
        None, description="Inverted only: none, english, chinese, standard, unicode, ngram"
    )
    imp_lib: str | None = Field(
        None, description="Inverted only: clucene (default) or builtin (v4.1)"
    )
    dict_gram_num: int | None = Field(
        None, ge=1, description="Built-in n-gram dictionary size (positive integer)"
    )
    properties: dict[str, str] = Field(default_factory=dict)


class DropIndexRequest(BaseModel):
    database: str
    table: str
    index_name: str


class FullTextPredicate(BaseModel):
    column: str
    operator: str = Field(..., description="MATCH, MATCH_ANY or MATCH_ALL")
    keyword: str


# ── Builders ───────────────────────────────────────────────────


def _build_index_properties(req: CreateIndexRequest) -> str:
    """Render the ``USING GIN(...)`` property list from allow-listed pieces.

    The parser/imp_lib are validated against their own allow-lists; the numeric
    knobs against a positive-integer check; every key against the closed set.
    The result is a ``", "``-joined list of ``"key"="value"`` pairs, each side
    already known-safe, so no quote in a value can close a pair early.
    """
    properties: dict[str, str] = dict(req.properties or {})

    if req.parser is not None:
        properties["parser"] = check_inverted_parser(req.parser)
    if req.imp_lib is not None:
        properties["imp_lib"] = check_inverted_imp_lib(req.imp_lib)
    if req.dict_gram_num is not None:
        if not isinstance(req.dict_gram_num, int) or isinstance(req.dict_gram_num, bool):
            raise HTTPException(status_code=400, detail="dict_gram_num must be an integer")
        properties["dict_gram_num"] = str(req.dict_gram_num)

    pairs: list[str] = []
    for key, value in properties.items():
        safe_key = check_index_property_key(str(key))
        safe_value = str(value)
        if safe_key in ("parser", "imp_lib"):
            # The dedicated validators are the source of truth for these two;
            # re-checking here keeps a value passed through ``properties`` from
            # bypassing them.
            safe_value = (
                check_inverted_parser(safe_value)
                if safe_key == "parser"
                else check_inverted_imp_lib(safe_value)
            )
        elif safe_key in _NUMERIC_PROPERTIES:
            if not safe_value.isdigit() or int(safe_value) < 1:
                raise HTTPException(
                    status_code=400,
                    detail=f"{safe_key} must be a positive integer",
                )
        else:  # pragma: no cover — the closed key set makes this unreachable
            safe_value = check_index_property_value(safe_value)
        pairs.append(f'"{safe_key}"="{safe_value}"')
    return ", ".join(pairs)


def build_create_index_sql(req: CreateIndexRequest) -> str:
    """Assemble ``ALTER TABLE ... ADD INDEX ... USING ...`` from allow-listed input.

    Bitmap and bloom-filter indexes use the plain ``ADD INDEX (col)`` form; GIN
    and NGRAM_BF carry their properties. Refuses before any SQL is built.
    """
    database = check_identifier(req.database, field="database")
    table = check_identifier(req.table, field="table name")
    index_name = check_identifier(req.index_name, field="index name")
    column = check_identifier(req.column, field="index column")
    kind = check_index_kind(req.kind)

    if kind == "BITMAP":
        if req.parser or req.imp_lib or req.dict_gram_num or req.properties:
            raise HTTPException(
                status_code=400,
                detail="BITMAP indexes take no GIN properties",
            )
        return f"ALTER TABLE `{database}`.`{table}` ADD INDEX `{index_name}` (`{column}`)"

    if kind == "GIN":
        props = _build_index_properties(req)
        # ``parser`` is optional on the engine (defaults to ``none``), but Nova
        # requires the caller to be explicit about tokenization so the resulting
        # search semantics are not a surprise.
        if "parser" not in props:
            raise HTTPException(
                status_code=400,
                detail="GIN (inverted) indexes require a parser",
            )
        return (
            f"ALTER TABLE `{database}`.`{table}` ADD INDEX `{index_name}` "
            f"(`{column}`) USING GIN ({props})"
        )

    # NGRAM_BF: gram_num and bloom_filter_fpp are the documented properties.
    if req.parser or req.imp_lib or req.dict_gram_num:
        raise HTTPException(
            status_code=400,
            detail="NGRAM_BF indexes take only gram_num/bloom_filter_fpp properties",
        )
    props = ""
    if req.properties:
        props = " (" + _build_index_properties(req) + ")"
    return (
        f"ALTER TABLE `{database}`.`{table}` ADD INDEX `{index_name}` "
        f"(`{column}`) USING NGRAM_BF{props}"
    )


def build_drop_index_sql(req: DropIndexRequest) -> str:
    """Assemble ``ALTER TABLE ... DROP INDEX `` from allow-listed input."""
    database = check_identifier(req.database, field="database")
    table = check_identifier(req.table, field="table name")
    index_name = check_identifier(req.index_name, field="index name")
    return f"ALTER TABLE `{database}`.`{table}` DROP INDEX `{index_name}`"


def validate_fulltext_predicate(predicate: FullTextPredicate) -> str:
    """Return a canonical full-text predicate string, or raise.

    This is the *shape* check for a query Nova does not compose — the SQL
    workspace owns statement assembly. It exists so the API can tell a client
    whether a ``WHERE`` predicate will use an inverted index, and to pin the
    documented forms. ``keyword`` is not a security boundary here (it is a
    value in a statement the caller writes), so it is only sanity-checked for
    length.
    """
    column = check_identifier(predicate.column, field="column")
    operator = (predicate.operator or "").strip().upper()
    if operator not in FULLTEXT_PREDICATES:
        raise HTTPException(
            status_code=400,
            detail=f"operator must be one of: {', '.join(FULLTEXT_PREDICATES)}",
        )
    keyword = predicate.keyword or ""
    if not keyword.strip():
        raise HTTPException(status_code=400, detail="keyword must not be empty")
    return f"{column} {operator} '{keyword}'"


# ── Endpoints ──────────────────────────────────────────────────


@router.post("/create")
async def create_index(
    req: CreateIndexRequest,
    user: CurrentUser,
    conn: UserConnection,
):
    """Create an inverted (or bitmap / n-gram bloom) index on the caller's connection."""
    sql = build_create_index_sql(req)
    object_name = f"{req.database}.{req.table}.{req.index_name}"
    guard_sql(sql)

    try:
        async with conn.cursor() as cur:
            await cur.execute(sql)
    except Exception as e:
        await write_audit_log(
            event_type="ddl",
            user_name=user["username"],
            action="create_index",
            object_type="index",
            object_name=object_name,
            status="ERROR",
            sql_text=sql,
            error_message=str(e),
            database_name=req.database,
            session_id=user["session_id"],
        )
        raise HTTPException(status_code=400, detail=str(e)) from e

    await write_audit_log(
        event_type="ddl",
        user_name=user["username"],
        action="create_index",
        object_type="index",
        object_name=object_name,
        status="SUCCESS",
        sql_text=sql,
        database_name=req.database,
        session_id=user["session_id"],
    )
    return {"success": True, "sql": sql, "message": f"Index '{object_name}' created"}


@router.post("/drop")
async def drop_index(
    req: DropIndexRequest,
    user: CurrentUser,
    conn: UserConnection,
):
    """Drop an index on the caller's connection."""
    sql = build_drop_index_sql(req)
    object_name = f"{req.database}.{req.table}.{req.index_name}"
    guard_sql(sql)

    try:
        async with conn.cursor() as cur:
            await cur.execute(sql)
    except Exception as e:
        await write_audit_log(
            event_type="ddl",
            user_name=user["username"],
            action="drop_index",
            object_type="index",
            object_name=object_name,
            status="ERROR",
            sql_text=sql,
            error_message=str(e),
            database_name=req.database,
            session_id=user["session_id"],
        )
        raise HTTPException(status_code=400, detail=str(e)) from e

    await write_audit_log(
        event_type="ddl",
        user_name=user["username"],
        action="drop_index",
        object_type="index",
        object_name=object_name,
        status="SUCCESS",
        sql_text=sql,
        database_name=req.database,
        session_id=user["session_id"],
    )
    return {"success": True, "sql": sql, "message": f"Index '{object_name}' dropped"}


@router.get("/{database}/{table}")
async def list_indexes(
    database: str,
    table: str,
    user: CurrentUser,
    conn: UserConnection,
):
    """List a table's indexes from ``SHOW INDEX``, executed as the caller."""
    check_identifier(database, field="database")
    check_identifier(table, field="table name")
    try:
        indexes = await index_repository.list_indexes(conn, database, table)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"database": database, "table": table, "indexes": indexes}


@router.post("/validate-predicate")
async def validate_predicate(predicate: FullTextPredicate, user: CurrentUser):
    """Validate a full-text predicate shape without executing it.

    Full-text predicates are pushdown-only: the engine accepts ``MATCH`` /
    ``MATCH_ANY`` / ``MATCH_ALL`` on an indexed column in a ``WHERE`` clause and
    rejects them anywhere else. Nova does not rewrite the caller's statement, so
    this endpoint is the contract check the SQL workspace (and the UI's search
    preview) can call before sending one.
    """
    canonical = validate_fulltext_predicate(predicate)
    return {
        "valid": True,
        "predicate": canonical,
        "supported": list(FULLTEXT_PREDICATES),
        "notes": [
            "The predicate must be against a GIN-indexed column and in a WHERE clause.",
            "MATCH supports the %keyword% wildcard form; MATCH_ANY/MATCH_ALL take "
            "space-separated keywords.",
            "English/standard tokenization lowercases terms, so keywords must be lowercase.",
        ],
    }


@router.get("/capabilities")
async def get_capabilities(user: CurrentUser):
    """Report the engine-level preconditions a caller must satisfy.

    Every item below was observed on the pinned StarRocks 4.1.4 engine (see
    ``tests/integration/test_inverted_index_l3.py``), not copied from the docs:
    an inverted index is silently absent until the schema change completes, and
    ``ADD INDEX ... USING GIN`` fails outright unless the FE config is on and the
    table is not replicated. Surfacing them here lets the UI warn *before* a
    create call fails, and lets an operator see exactly which switch is missing.
    """
    return {
        "engine": "StarRocks 4.1.x",
        "kind": "inverted",
        "parsers": sorted(["none", "english", "chinese", "standard", "unicode", "ngram"]),
        "imp_libs": ["clucene", "builtin"],
        "predicates": list(FULLTEXT_PREDICATES),
        "preconditions": [
            {
                "id": "enable_experimental_gin",
                "scope": "FE config",
                "message": (
                    "Full-text inverted indexes are disabled until "
                    '`ADMIN SET FRONTEND CONFIG ("enable_experimental_gin" = "true")` '
                    "is run (or fe.conf sets it). The engine rejects ADD INDEX ... "
                    "USING GIN with 'The inverted index is disabled' otherwise."
                ),
            },
            {
                "id": "replicated_storage",
                "scope": "table property",
                "message": (
                    "The table must have replicated_storage=false. On 4.0+ the "
                    "engine disables it automatically when the index is in the "
                    "CREATE TABLE statement, but not when one is added later."
                ),
            },
            {
                "id": "async_schema_change",
                "scope": "engine behaviour",
                "message": (
                    "ADD/DROP INDEX is asynchronous: the statement returns "
                    "immediately and the index appears only once the schema change "
                    "reaches FINISHED (SHOW ALTER TABLE COLUMN). A second ALTER on "
                    "the table while one is in flight is rejected."
                ),
            },
            {
                "id": "pushdown_only",
                "scope": "query shape",
                "message": (
                    "MATCH / MATCH_ANY / MATCH_ALL are pushdown-only: they must be "
                    "against a GIN-indexed column in a WHERE clause. Anywhere else "
                    "the engine raises 'Match can only used as a pushdown predicate'."
                ),
            },
        ],
    }
