"""Migration verdict rules — pure functions, no I/O.

This is the domain core of the connector. It answers one question per object:
can Nova migrate it as-is (``migratable``), does it move but lose something the
operator must be told about (``lossy``), or can it not be migrated at all
(``skipped``)?

The rules are deliberately conservative and are grounded in the NOVA-84 research
(StarRocks 4.1.4, commit pin ``4a9848ed``):

* ``MASKING POLICY`` / ``ROW ACCESS POLICY`` have **no DDL export** in 4.1.4.
  They are always ``skipped`` with an explicit reason — silently dropping a
  policy is the failure mode the whole report exists to prevent.
* ``TASK`` / ``PIPE`` have no ``SHOW CREATE``; Nova reconstructs their DDL from
  ``information_schema``, which is lossy for the original ``SELECT``/properties.
  They are ``lossy``, never ``migratable``.
* Async materialized views are ``migratable`` via ``SHOW CREATE MATERIALIZED
  VIEW`` (which carries ``REFRESH``/``PARTITION BY``/``PROPERTIES``); **sync**
  MVs stay ``lossy`` on every surface because that statement only emits
  ``CREATE MATERIALIZED VIEW ... AS SELECT``.
* UDFs are ``lossy`` on every surface. Native SQL bodies are reconstructable
  from ``SHOW FULL FUNCTIONS`` (the body is in the ``Properties`` column) but the
  argument *names* are not exposed by any surface and are inferred from the body,
  so the result may differ; non-native (Java/Python jar) bodies cannot be carried
  at all.
* ``ACCOUNTADMIN`` is never offered for replication.
* ``replication_num`` / bucketing are deployment-specific and are remapped rather
  than copied; a table is still ``migratable`` (data + schema), and this is
  surfaced as a note, not a downgrade.

The module imports nothing from infrastructure so it stays trivially testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.modules.migration.schemas import MigrationVerdict, ObjectKind


@dataclass(frozen=True)
class Verdict:
    """The classification of one object."""

    verdict: MigrationVerdict
    reason: str
    notes: tuple[str, ...] = field(default_factory=tuple)


def _migratable(reason: str, *notes: str) -> Verdict:
    return Verdict(MigrationVerdict.MIGRATABLE, reason, tuple(notes))


def _lossy(reason: str, *notes: str) -> Verdict:
    return Verdict(MigrationVerdict.LOSSY, reason, tuple(notes))


def _skipped(reason: str, *notes: str) -> Verdict:
    return Verdict(MigrationVerdict.SKIPPED, reason, tuple(notes))


#: Reasons are user-facing strings. They are constants so a test can assert the
#: exact wording and a future translation layer has one place to key off.
REASON_MASKING_NO_DDL = (
    "MASKING POLICY has no DDL export in StarRocks 4.1.4; the policy cannot be "
    "carried to Nova and would be lost without an explicit skip."
)
REASON_ROW_ACCESS_NO_DDL = (
    "ROW ACCESS POLICY has no DDL export in StarRocks 4.1.4; the policy cannot "
    "be carried to Nova and would be lost without an explicit skip."
)
REASON_TASK_RECONSTRUCTED = (
    "TASK has no SHOW CREATE in StarRocks 4.1.4; Nova reconstructs CREATE TASK "
    "from information_schema.tasks (SCHEDULE + DEFINITION). Properties and the "
    "exact original statement may differ, so the definition is lossy."
)
REASON_PIPE_NO_SHOW_CREATE = (
    "PIPE has no SHOW CREATE in StarRocks 4.1.4, and information_schema.pipes "
    "exposes only properties/state with no SELECT body, so the ingest definition "
    "cannot be reconstructed and is not offered for migration."
)
REASON_SYNC_MV_LOSSY = (
    "Sync materialized view: SHOW CREATE MATERIALIZED VIEW emits only "
    "'CREATE MATERIALIZED VIEW ... AS SELECT' with no PROPERTIES/PARTITION BY/"
    "REFRESH, so the definition is lossy on every surface."
)
REASON_ASYNC_MV_MIGRATABLE = (
    "Async materialized view: SHOW CREATE MATERIALIZED VIEW carries REFRESH, "
    "PARTITION BY and PROPERTIES."
)
REASON_TABLE_MIGRATABLE = (
    "Table schema and data migrate; distribution-level properties are remapped "
    "for the target cluster."
)
REASON_VIEW_MIGRATABLE = "View definition migrates via SHOW CREATE VIEW."
REASON_FUNCTION_MIGRATABLE = (
    "Native SQL function: the body is recoverable from SHOW FULL FUNCTIONS."
)
REASON_FUNCTION_LOSSY = (
    "Function body is non-native (jar/UDF payload) and is not carried by the "
    "SHOW FULL FUNCTIONS column set, so the definition is lossy."
)
REASON_SQL_FUNCTION_ARG_NAMES_LOSSY = (
    "SQL function: StarRocks 4.1.4 has no SHOW CREATE FUNCTION and the "
    "SHOW FULL FUNCTIONS signature carries argument types only, so argument "
    "names are reconstructed from the body's backticked identifiers and may "
    "differ from the original definition."
)

#: Distribution/bucketing properties that are deployment-specific. They do not
#: downgrade a table, but they are reported so the operator can review remapping.
REMAP_NOTES: tuple[str, ...] = (
    "replication_num is deployment-specific and will be remapped for the target.",
    "Bucketing/distribution may be reassigned by the target engine.",
)

#: MV refresh type values that denote an async MV. Everything else (``SYNC``,
#: missing) is treated as sync/lossy.
ASYNC_REFRESH_TYPES = frozenset({"ASYNC", "MANUAL"})

#: Function types that are native SQL and therefore reproducible from DDL alone.
NATIVE_FUNCTION_TYPES = frozenset({"SCALAR", "AGGREGATE", "SQL"})


def classify_table() -> Verdict:
    """A base table: schema + data migrate, deployment props are remapped."""
    return _migratable(REASON_TABLE_MIGRATABLE, *REMAP_NOTES)


def classify_view() -> Verdict:
    return _migratable(REASON_VIEW_MIGRATABLE)


def classify_materialized_view(refresh_type: str | None) -> Verdict:
    """Async MVs migrate losslessly; sync MVs are lossy on every surface."""
    if refresh_type and refresh_type.upper() in ASYNC_REFRESH_TYPES:
        return _migratable(REASON_ASYNC_MV_MIGRATABLE)
    return _lossy(REASON_SYNC_MV_LOSSY)


def classify_function(function_type: str | None) -> Verdict:
    """Classify a UDF.

    Only native SQL bodies are reconstructable, and even then the argument names
    are inferred rather than read (no ``SHOW CREATE FUNCTION`` exists on 4.1.4),
    so the verdict is ``lossy`` with an explicit reason. A jar/payload body
    (Java/Python) cannot be carried at all and is ``lossy`` for a different
    reason. Neither is ever reported ``migratable``: the operator must see that
    the function needs review.
    """
    if function_type and function_type.upper() in NATIVE_FUNCTION_TYPES:
        return _lossy(REASON_SQL_FUNCTION_ARG_NAMES_LOSSY)
    return _lossy(REASON_FUNCTION_LOSSY)


#: The identifier form StarRocks emits for an SQL UDF argument in the body.
_ARG_IDENTIFIER = re.compile(r"`([^`]+)`")


def reconstruct_sql_function_ddl(
    *,
    name: str,
    signature: str | None,
    body: str | None,
    scope: str = "database",
) -> str | None:
    """Rebuild a ``CREATE [GLOBAL] FUNCTION`` for an SQL UDF, or ``None``.

    StarRocks 4.1.4 exposes no ``SHOW CREATE FUNCTION``; the only definition
    surface is ``SHOW FULL FUNCTIONS``, whose ``Signature`` carries argument
    *types* (``f_add(INT,INT)``) and whose ``Properties`` carries the body
    (``"`x` + `y`"``). The argument *names* are not exposed anywhere, so they are
    recovered from the body's backticked identifiers in first-appearance order —
    which is how the engine itself references them.

    This is deliberately best-effort: if the identifier count cannot cover the
    signature arity, ``None`` is returned rather than a statement that would fail
    or silently differ. The caller reports the function as ``lossy`` regardless.
    """
    if not name or not signature or not body:
        return None
    open_paren = signature.find("(")
    close_paren = signature.rfind(")")
    if open_paren < 0 or close_paren < open_paren:
        return None
    arg_types = [part.strip() for part in signature[open_paren + 1 : close_paren].split(",")]
    arg_types = [part for part in arg_types if part]
    if not arg_types:
        return None

    names: list[str] = []
    for match in _ARG_IDENTIFIER.finditer(body):
        candidate = match.group(1)
        if candidate not in names:
            names.append(candidate)
    if len(names) < len(arg_types):
        return None

    args_sql = ", ".join(
        f"`{arg_name}` {arg_type}" for arg_name, arg_type in zip(names, arg_types, strict=False)
    )
    prefix = "GLOBAL " if scope == "global" else ""
    qualified = f"`{name}`" if scope == "global" else f"`{name}`"
    return f"CREATE {prefix}FUNCTION {qualified}({args_sql}) RETURNS {body}"


def classify_task() -> Verdict:
    """A TASK is reconstructed from ``information_schema.tasks``.

    ``SCHEDULE`` and ``DEFINITION`` are both exposed, so a ``CREATE TASK`` can be
    rebuilt. It stays ``lossy``: ``PROPERTIES`` and the exact original statement
    are not all carried, and the reconstruction is best-effort.
    """
    return _lossy(REASON_TASK_RECONSTRUCTED)


def reconstruct_task_ddl(
    *,
    name: str,
    schedule: str | None,
    definition: str | None,
    properties: str | None = None,
) -> str | None:
    """Rebuild a ``CREATE TASK`` from ``information_schema.tasks``, or ``None``.

    ``definition`` is the SQL body and ``schedule`` the cadence. Both are
    required: a task without a body or a schedule is not reproducible, and Nova
    returns ``None`` rather than emitting an invalid statement. ``properties`` is
    carried when present, verbatim.
    """
    if not name or not definition or not schedule:
        return None
    schedule = schedule.strip()
    body = definition.strip().rstrip(";")
    if not schedule or not body:
        return None
    # The engine stores the schedule already in its clause form
    # (e.g. "EVERY(INTERVAL 1 HOUR)"); use it as-is rather than re-parsing.
    properties_sql = ""
    if properties and properties.strip() and properties.strip().upper() != "NULL":
        raw = properties.strip()
        properties_sql = (
            f"\nPROPERTIES ({raw})" if not raw.startswith("(") else f"\nPROPERTIES {raw}"
        )
    return f"CREATE TASK `{name}`\nSCHEDULE {schedule}{properties_sql}\nAS {body}"


def classify_pipe() -> Verdict:
    return _lossy(REASON_PIPE_NO_SHOW_CREATE)


def classify_masking_policy() -> Verdict:
    return _skipped(REASON_MASKING_NO_DDL)


def classify_row_access_policy() -> Verdict:
    return _skipped(REASON_ROW_ACCESS_NO_DDL)


def classify(
    kind: ObjectKind, *, refresh_type: str | None = None, function_type: str | None = None
) -> Verdict:
    """Dispatch to the rule for ``kind``.

    Keeping the dispatch here means the service never branches on object kind
    itself; adding a kind is one entry point plus its rule.
    """
    match kind:
        case ObjectKind.TABLE:
            return classify_table()
        case ObjectKind.VIEW:
            return classify_view()
        case ObjectKind.MATERIALIZED_VIEW:
            return classify_materialized_view(refresh_type)
        case ObjectKind.FUNCTION:
            return classify_function(function_type)
        case ObjectKind.TASK:
            return classify_task()
        case ObjectKind.PIPE:
            return classify_pipe()
        case ObjectKind.MASKING_POLICY:
            return classify_masking_policy()
        case ObjectKind.ROW_ACCESS_POLICY:
            return classify_row_access_policy()
    raise ValueError(f"Unknown object kind: {kind}")  # pragma: no cover
