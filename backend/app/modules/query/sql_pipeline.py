"""Shared preparation of user-supplied SQL before it reaches StarRocks.

Every path where a user hands Nova a SQL statement that Nova then executes must
perform the same four steps, in the same order:

1. **Guard** — reject the statements the SQL guard blocks, and require explicit
   confirmation for destructive ones.
2. **Translate** — rewrite ``@stage`` references to ``FILES()``.
3. **Inject** — add storage credentials to every ``FILES()`` call.
4. **Redact** — produce the credential-free form that may leave the process.

This module owns those steps so the guarantee is stated once. It exists because
``ml_engine`` executed ``training_sql`` with none of them: no guard, no
translation, no injection, no redaction (NOVA-28). Two implementations of a
credential-bearing pipeline drift, and the drift is a leak — ``query`` had the
pipeline and ``ml_engine`` did not, which is exactly the failure this avoids.

Ordering is load-bearing. The guard runs on the *normalized* statement before
any translation, so a statement the user wrote is judged as written; redaction
runs on the *translated* statement, because that is the text that carries the
injected credentials.
"""

from __future__ import annotations

import re

from app.common.sql_guard import (
    guard_sql,
    is_destructive_sql,
    redact_sql_credentials,
    split_sql_statements,
)
from app.core.exceptions import ForbiddenSQLError
from app.modules.query.dialect.injector import get_credential_params
from app.modules.query.dialect.parser import ParsedSQL, parse_sql
from app.modules.query.dialect.translator import StorageConfig, translate_stage_query


class PreparedSQL:
    """The result of preparing one statement for execution.

    ``engine_sql`` is what StarRocks receives and **carries real credentials** —
    it must never be persisted, returned, or logged. ``redacted_sql`` is the
    only form that may leave the process. Keeping both on one object, with the
    names stating which is which, makes the wrong choice visible at every use
    site instead of relying on a comment.
    """

    __slots__ = ("engine_sql", "redacted_sql", "warnings", "csv_columns", "parsed")

    def __init__(
        self,
        *,
        engine_sql: str,
        redacted_sql: str,
        warnings: list[str],
        csv_columns: list[str] | None,
        parsed: ParsedSQL,
    ) -> None:
        self.engine_sql = engine_sql
        self.redacted_sql = redacted_sql
        self.warnings = warnings
        self.csv_columns = csv_columns
        self.parsed = parsed


def guard_user_statement(sql: str, *, confirm_destructive: bool = False) -> None:
    """Apply the SQL guard and the destructive-confirmation rule to ``sql``.

    Splits first, because the API accepts multi-statement scripts: a guard
    anchored on the whole blob is blind to everything after the first ``;``.
    A single statement carries no ``;`` outside a string literal, so it comes
    back unchanged.

    Raises:
        ForbiddenSQLError: if any statement is blocked, or is destructive and
            ``confirm_destructive`` is false.
    """
    for statement in split_sql_statements(sql) or [sql]:
        guard_sql(statement)
        if is_destructive_sql(statement) and not confirm_destructive:
            raise ForbiddenSQLError(
                "Destructive SQL requires confirmation before execution."
            ) from None


def redact_for_output(sql: str) -> str:
    """The credential-free form of a statement, safe to store or return.

    Thin wrapper over the guard module's redactor, exposed here so callers of
    this pipeline have one obvious function to reach for and do not import the
    guard directly for redaction.
    """
    return redact_sql_credentials(sql)


async def prepare_stage_sql(
    sql: str,
    *,
    stage_configs: dict[str, StorageConfig] | None = None,
    csv_params: dict[str, str] | None = None,
    csv_columns: list[str] | None = None,
) -> PreparedSQL:
    """Translate ``@stage`` references and inject credentials into ``sql``.

    Pure with respect to everything except the values passed in: stage configs,
    detected CSV parameters and the FILES() credentials are all arguments or
    read from config at this point, so a caller can drive every branch without
    a database. The engine call itself is the caller's business — this returns
    the statement to run.

    ``csv_params``/``csv_columns`` are separate arguments rather than detected
    here because detection needs a boto3 read of the file, which is I/O and
    belongs at the edge; ``query.service`` performs that read and passes the
    result in.
    """
    parsed = parse_sql(sql)
    if not parsed.stage_refs:
        engine_sql = sql
        return PreparedSQL(
            engine_sql=engine_sql,
            redacted_sql=redact_sql_credentials(engine_sql),
            warnings=[],
            csv_columns=None,
            parsed=parsed,
        )

    executed_sql, warnings = translate_stage_query(parsed, stage_configs or {})

    if csv_params:
        executed_sql = _inject_files_params(executed_sql, csv_params)

    credentials = get_credential_params("s3")
    if credentials:
        executed_sql = _inject_files_params(executed_sql, credentials)

    return PreparedSQL(
        engine_sql=executed_sql,
        redacted_sql=redact_sql_credentials(executed_sql),
        warnings=warnings,
        csv_columns=csv_columns,
        parsed=parsed,
    )


def _inject_files_params(sql: str, params: dict[str, str]) -> str:
    """Add ``params`` to every ``FILES()`` call in ``sql`` that lacks them.

    A single ``FILES()`` per statement in practice; the substitution covers all
    of them so a statement with two stage references is not half-injected. A
    second pass over an already-injected statement is a no-op, which matters
    because this runs once for CSV properties and again for credentials.

    The "already present" test is **per key**, and deliberately not the obvious
    `if "access_key" in content` guard. That guard is wrong here: the
    translator emits credentials itself, so on the CSV pass it saw an
    `access_key` belonging to the *credential* group and skipped the CSV
    properties entirely. The result was a stage query returning the file's raw
    line text as one column instead of parsed columns — `csv.column_separator`
    and `csv.skip_header` never reached the engine. Keying on the names being
    injected is what makes the two passes independent.
    """

    def _inject(match: re.Match[str]) -> str:
        content = match.group(1)
        # Per key, not per group: a partially-injected statement must gain only
        # the keys it lacks. Injecting the whole group when *some* key is
        # present would emit a duplicate parameter, which the engine rejects.
        missing = [
            (key, value) for key, value in params.items() if f"'{key}'" not in content
        ]
        if not missing:
            return match.group(0)
        parts = [f"'{key}'='{value}'" for key, value in missing]
        return f"FILES({content}, {', '.join(parts)})"

    return re.sub(r"FILES\(([^)]+)\)", _inject, sql)
