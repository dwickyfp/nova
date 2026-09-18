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
* UDFs reconstructed from ``SHOW FULL FUNCTIONS`` are ``lossy`` when they carry a
  non-native body (Java/Python jar) the column set cannot reproduce.
* ``ACCOUNTADMIN`` is never offered for replication.
* ``replication_num`` / bucketing are deployment-specific and are remapped rather
  than copied; a table is still ``migratable`` (data + schema), and this is
  surfaced as a note, not a downgrade.

The module imports nothing from infrastructure so it stays trivially testable.
"""

from __future__ import annotations

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
REASON_TASK_NO_SHOW_CREATE = (
    "TASK has no SHOW CREATE in StarRocks 4.1.4; Nova reconstructs a partial "
    "definition from information_schema, so the original SQL and properties are "
    "lossy."
)
REASON_PIPE_NO_SHOW_CREATE = (
    "PIPE has no SHOW CREATE in StarRocks 4.1.4; Nova reconstructs the DDL from "
    "information_schema properties, and the original SELECT body is lossy."
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
REASON_FUNCTION_MIGRATABLE = "Native SQL function definition migrates via its reconstructed DDL."
REASON_FUNCTION_LOSSY = (
    "Function body is non-native (jar/UDF payload) and is not carried by the "
    "SHOW FULL FUNCTIONS column set, so the definition is lossy."
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
    if function_type and function_type.upper() in NATIVE_FUNCTION_TYPES:
        return _migratable(REASON_FUNCTION_MIGRATABLE)
    return _lossy(REASON_FUNCTION_LOSSY)


def classify_task() -> Verdict:
    return _lossy(REASON_TASK_NO_SHOW_CREATE)


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
