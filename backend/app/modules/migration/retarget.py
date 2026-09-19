"""DDL retargeting — turn a source ``SHOW CREATE`` statement into one that can
be replayed against a Nova target database.

StarRocks emits ``SHOW CREATE`` DDL in a shape tied to the *source* cluster:

* the created object is **unqualified** — ``CREATE TABLE `t` (...)`` — so
  replaying it requires qualifying it with the target database;
* internal references keep the **source database** — ``... FROM src_db.other`` —
  so the body must be repointed;
* the ``PROPERTIES`` block contains **deployment-specific** keys
  (``replication_num``, ``replicated_storage``, ``storage_medium``,
  ``compression``, ``fast_schema_evolution``) that describe the source cluster's
  storage and must not be forced onto the target;
* a **view** may carry ``SECURITY NONE`` and an MV carries ``REFRESH`` /
  ``PARTITION BY`` — those are semantic and are preserved verbatim.

This module is pure: it takes strings and returns strings, with no I/O, so it is
trivially unit-testable. It performs **no execution** — the apply path is gated
separately (issue #7).

Design rule: when a statement cannot be retargeted with confidence,
``RetargetError`` is raised. A statement that is silently mis-rewritten is worse
than one the operator has to fix by hand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Keys in a ``PROPERTIES`` block that describe the *source* deployment and must
#: be dropped rather than copied. Matching is case-insensitive.
DEPLOYMENT_PROPERTY_KEYS: frozenset[str] = frozenset(
    {
        "replication_num",
        "replicated_storage",
        "storage_medium",
        "storage_cooldown_time",
        "compression",
        "fast_schema_evolution",
        "enable_persistent_index",
        "bloom_filter_columns",
        "colocate_with",
        "replication_num_min",
    }
)

#: Object head keywords that carry a name we must qualify. Order matters: the
#: longer ``MATERIALIZED VIEW`` must be tried before ``VIEW``.
_OBJECT_HEADS: tuple[tuple[str, str], ...] = (
    ("CREATE GLOBAL FUNCTION", "function"),
    ("CREATE MATERIALIZED VIEW", "materialized_view"),
    ("CREATE OR REPLACE VIEW", "view"),
    ("CREATE VIEW", "view"),
    ("CREATE TABLE", "table"),
    ("CREATE FUNCTION", "function"),
    ("CREATE TASK", "task"),
)

#: ``db.name`` / ``db`.`name`` reference inside a statement body or head.
_QUALIFIED_REF = re.compile(
    r"(?P<db>`?[A-Za-z_][A-Za-z0-9_]*`?)\.(?P<name>`[^`]+`|[A-Za-z_][A-Za-z0-9_]*)"
)

#: A ``PROPERTIES ( ... )`` block. Non-greedy, DOTALL: the engine emits the block
#: across lines with double-quoted keys and values.
_PROPERTIES_BLOCK = re.compile(r"\bPROPERTIES\s*\((?P<body>.*?)\)", re.IGNORECASE | re.DOTALL)

#: One ``"key" = "value"`` / ``'key'='value'`` entry inside a PROPERTIES body.
_PROPERTY_ENTRY = re.compile(
    r"""["'](?P<key>[^"']+)["']\s*=\s*["'](?P<value>[^"']*)["']""",
    re.DOTALL,
)


class RetargetError(ValueError):
    """A statement could not be retargeted with confidence."""


@dataclass(frozen=True)
class Retargeted:
    """The result of retargeting one statement."""

    statement: str
    kind: str
    object_name: str
    target_database: str
    #: Deployment property keys that were removed, for the operator report.
    dropped_properties: tuple[str, ...] = field(default_factory=tuple)
    #: The replication factor applied to the target, when one was supplied.
    applied_replication_num: int | None = None


def _quote(identifier: str) -> str:
    """Backtick-quote a bare identifier, escaping any embedded backtick."""
    return "`" + identifier.replace("`", "``") + "`"


def _unquote(identifier: str) -> str:
    text = identifier.strip()
    if text.startswith("`") and text.endswith("`") and len(text) >= 2:
        return text[1:-1].replace("``", "`")
    return text


def _split_head(statement: str) -> tuple[str, str, str]:
    """Split ``head ( body`` into the leading keyword+name, the ``(`` and rest.

    The first ``(`` in a ``CREATE TABLE`` / ``CREATE VIEW`` marks the column list.
    A function's first ``(`` is its argument list, so the caller handles that
    separately. Returns ``(head, open_paren, tail)`` where ``open_paren`` is
    ``"("`` or ``""`` if there is none before a ``;``.
    """
    open_paren = statement.find("(")
    if open_paren < 0:
        return statement.rstrip().rstrip(";"), "", ""
    return statement[:open_paren], "(", statement[open_paren + 1 :]


def _detect_kind(statement: str) -> tuple[str, str]:
    """Return ``(kind, matched_head_keyword)`` for a CREATE statement."""
    upper = statement.lstrip().upper()
    for keyword, kind in _OBJECT_HEADS:
        if upper.startswith(keyword):
            return kind, keyword
    raise RetargetError("Statement is not a recognised CREATE object")


def _qualify_object(
    statement: str, kind: str, source_db: str, target_db: str, object_name: str
) -> str:
    """Qualify the created object name with the target database.

    ``SHOW CREATE`` emits an unqualified name for a database-scoped object. If the
    name is already qualified with ``source_db`` it is repointed; if it is
    qualified with a *different* database the statement is refused (the operator
    is looking at a cross-database object the connector does not model).
    """
    stripped = statement.lstrip()
    if kind == "function" and stripped.upper().startswith("CREATE GLOBAL FUNCTION"):
        # A global function has no database qualifier at all.
        return statement
    if kind == "task":
        # ``CREATE TASK `name` …`` has no argument list before the name; the name
        # ends at the first whitespace after the backticked token.
        match = re.match(
            r"(?s)(?P<prefix>CREATE\s+TASK\s+)(?P<name>`[^`]+`|[A-Za-z_][A-Za-z0-9_]*)(?P<rest>.*)",
            stripped,
        )
        if not match:
            raise RetargetError("Could not locate the task name in CREATE TASK")
        bare_name = _unquote(match.group("name"))
        if bare_name != object_name:
            raise RetargetError(f"DDL creates '{bare_name}' but the object is '{object_name}'")
        return (
            f"{match.group('prefix')}{_quote(target_db)}.{_quote(object_name)}{match.group('rest')}"
        )

    first_paren = statement.find("(")
    if first_paren < 0:
        raise RetargetError("CREATE object has no argument/column list")
    head, rest = statement[:first_paren], statement[first_paren:]

    # Never touch the keyword portion; rewrite only the trailing name token.
    match = re.search(r"`[^`]+`|[A-Za-z_][A-Za-z0-9_]*\s*$", head)
    if not match:
        raise RetargetError("Could not locate the object name in the CREATE header")
    name_token = match.group(0).strip()
    prefix = head[: match.start()]

    # An already-qualified name arrives as `db`.`name`; the regex above only
    # catches the last token, so check the prefix for a trailing `db` + dot.
    if prefix.rstrip().endswith("."):
        prefix_db = prefix[: prefix.rstrip().rfind(".")].strip().split("`")[-2]
        if prefix_db not in (source_db, target_db):
            raise RetargetError(f"Object is qualified with '{prefix_db}', not the source database")
        prefix = prefix[: prefix.rstrip().rfind(".")]

    bare_name = _unquote(name_token)
    if bare_name != object_name:
        raise RetargetError(f"DDL creates '{bare_name}' but the object is '{object_name}'")
    return f"{prefix}{_quote(target_db)}.{_quote(object_name)}{rest}"


#: Keys that are remapped rather than dropped when the target factor is known.
_REPLICATION_KEYS: frozenset[str] = frozenset({"replication_num", "replication_num_min"})


def _drop_deployment_properties(
    statement: str, *, target_replication_num: int | None = None
) -> tuple[str, tuple[str, ...]]:
    """Remove deployment-specific keys from every ``PROPERTIES`` block.

    ``replication_num`` is special: dropping it lets the target's *default* apply,
    which may itself be unsatisfiable on a small target (e.g. default 3 on a
    single-BE cluster). When ``target_replication_num`` is supplied the key is
    rewritten to that safe value instead of removed; otherwise it is dropped.

    Returns the statement and the list of dropped keys. A block that becomes
    empty is removed entirely (an empty ``PROPERTIES ()`` is a syntax error).
    """
    dropped: list[str] = []

    def _rewrite(match: re.Match[str]) -> str:
        body = match.group("body")
        kept: list[str] = []
        for entry in _PROPERTY_ENTRY.finditer(body):
            key = entry.group("key").strip().lower()
            if key in _REPLICATION_KEYS and target_replication_num is not None:
                kept.append(f'"replication_num" = "{target_replication_num}"')
                continue
            if key in DEPLOYMENT_PROPERTY_KEYS:
                dropped.append(key)
                continue
            kept.append(entry.group(0))
        if not kept:
            return ""
        return "PROPERTIES (" + ", ".join(kept) + ")"

    rewritten = _PROPERTIES_BLOCK.sub(_rewrite, statement)
    # Tidy a doubled space / trailing comma left where a block was removed.
    rewritten = re.sub(r"[ \t]+", " ", rewritten)
    rewritten = re.sub(r"\s+,", ",", rewritten)
    return rewritten.strip(), tuple(sorted(set(dropped)))


def _repoint_references(statement: str, source_db: str, target_db: str) -> str:
    """Repoint ``source_db.object`` references to the target database.

    Only the exact source database is rewritten (case-insensitively); a
    reference to any other database is left untouched — it is an external
    dependency the operator must resolve, not something this connector invents.
    """
    source_lower = source_db.lower()
    target_quoted = _quote(target_db)

    def _rewrite(match: re.Match[str]) -> str:
        db = _unquote(match.group("db"))
        name = match.group("name")
        if db.lower() != source_lower:
            return match.group(0)
        if name.startswith("`"):
            return f"{target_quoted}.{name}"
        return f"{target_quoted}.{_quote(name)}"

    return _QUALIFIED_REF.sub(_rewrite, statement)


#: Object heads that accept an ``IF NOT EXISTS`` guard, in match order. A
#: ``CREATE OR REPLACE VIEW`` already carries its own replace semantics.
_IDEMPOTENT_HEADS: tuple[tuple[str, str], ...] = (
    ("CREATE TABLE IF NOT EXISTS", "CREATE TABLE IF NOT EXISTS"),
    ("CREATE TABLE", "CREATE TABLE IF NOT EXISTS"),
    ("CREATE VIEW IF NOT EXISTS", "CREATE VIEW IF NOT EXISTS"),
    ("CREATE VIEW", "CREATE VIEW IF NOT EXISTS"),
    ("CREATE MATERIALIZED VIEW IF NOT EXISTS", "CREATE MATERIALIZED VIEW IF NOT EXISTS"),
    ("CREATE MATERIALIZED VIEW", "CREATE MATERIALIZED VIEW IF NOT EXISTS"),
    ("CREATE FUNCTION IF NOT EXISTS", "CREATE FUNCTION IF NOT EXISTS"),
    ("CREATE FUNCTION", "CREATE FUNCTION IF NOT EXISTS"),
    ("CREATE GLOBAL FUNCTION IF NOT EXISTS", "CREATE GLOBAL FUNCTION IF NOT EXISTS"),
    ("CREATE GLOBAL FUNCTION", "CREATE GLOBAL FUNCTION IF NOT EXISTS"),
    # ``CREATE TASK`` has no ``IF NOT EXISTS`` form on 4.1.4; a re-run of a task
    # step would fail on an existing task, so it is left unguarded and the caller
    # sees the per-object error rather than a silent skip.
)


def make_idempotent(statement: str) -> str:
    """Add an ``IF NOT EXISTS`` guard so a re-run does not fail.

    A migration is expected to be re-runnable: a first attempt may have created
    some objects before a later step failed. ``CREATE OR REPLACE VIEW`` is left
    unchanged (it already replaces); any statement without a recognised head is
    returned verbatim rather than guessed at.
    """
    # ``CREATE OR REPLACE`` carries its own idempotency — never double it.
    if statement.lstrip().upper().startswith("CREATE OR REPLACE"):
        return statement
    for prefix, replacement in _IDEMPOTENT_HEADS:
        # ``str.replace`` with the longest-first ordering above upgrades an
        # already-guarded head to itself, which is a harmless no-op.
        if statement.lstrip().upper().startswith(prefix):
            leading = statement[: len(statement) - len(statement.lstrip())]
            return leading + replacement + statement.lstrip()[len(prefix) :]
    return statement


def retarget(
    ddl: str,
    *,
    kind: str,
    object_name: str,
    source_database: str,
    target_database: str,
    target_replication_num: int | None = None,
) -> Retargeted:
    """Retarget one ``SHOW CREATE`` statement onto ``target_database``.

    ``kind`` is the connector's object kind (``table`` / ``view`` /
    ``materialized_view`` / ``function``). ``object_name`` is the bare name from
    enumeration; it is used to verify the head was rewritten for the right
    object, so a mismatch fails loudly instead of producing a wrong statement.

    ``target_replication_num`` clamps the table replication factor to what the
    target can satisfy; when omitted the factor is dropped and the target default
    applies.
    """
    if not ddl or not ddl.strip():
        raise RetargetError("Empty DDL")
    # Source and target are different clusters, so the same database *name* on
    # both is the common case — it is not an error. Qualification and property
    # stripping are still required; only the reference rewrite becomes a no-op.

    statement = ddl.strip().rstrip(";").strip()
    detected_kind, _ = _detect_kind(statement)
    if kind in ("table", "view", "materialized_view", "function", "task") and detected_kind != kind:
        raise RetargetError(f"Expected a {kind} statement, got {detected_kind}")

    qualified = _qualify_object(statement, kind, source_database, target_database, object_name)
    repointed = _repoint_references(qualified, source_database, target_database)
    final, dropped = _drop_deployment_properties(
        repointed, target_replication_num=target_replication_num
    )

    if not final.upper().startswith("CREATE"):
        raise RetargetError("Retargeting did not produce a CREATE statement")
    return Retargeted(
        statement=final,
        kind=kind,
        object_name=object_name,
        target_database=target_database,
        dropped_properties=dropped,
        applied_replication_num=target_replication_num,
    )
