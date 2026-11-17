"""Backup and restore with a repository on a stage, for administrators.

``CREATE [READ ONLY] REPOSITORY name WITH BROKER ON LOCATION "@stage/backups/"``
is the only accepted repository form: the location is a stage folder and the
statement carries no properties, so no storage URL or credential is written by
the caller. The planner turns the location into one stage slot (``write``, or
``read`` for a read-only repository); ``StageRuntime`` authorizes it for the
active role before any secret is resolved, and the translator renders the
physical location followed by ``PROPERTIES(<the stage's storage credential>)``
at execution.

Unlike a one-off load or export, the engine keeps a repository's location and
credentials in its own metadata until ``DROP REPOSITORY``. That is why every
repository, backup and restore statement runs only with ``ACCOUNTADMIN`` as the
active role (``require_backup_admin``): the stored credential is the stage's
storage connection credential, and only an administrator may make the engine
hold it. ``docs/arch-13-sql-frontend.md`` records this decision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.sql_frontend.errors import SemanticError
from app.sql_frontend.parser import ParsedStatement
from app.sql_frontend.unload import decode_literal

FORM = "CREATE REPOSITORY"
ADMIN_ROLE = "ACCOUNTADMIN"

#: Statements that create, use, list or drop backup repositories and jobs.
BACKUP_CONTEXTS = frozenset(
    {
        "CreateRepositoryStatementContext",
        "DropRepositoryStatementContext",
        "ShowRepositoriesStatementContext",
        "BackupStatementContext",
        "CancelBackupStatementContext",
        "ShowBackupStatementContext",
        "RestoreStatementContext",
        "CancelRestoreStatementContext",
        "ShowRestoreStatementContext",
        "ShowSnapshotStatementContext",
    }
)

_LOCATION = re.compile(r"@(?P<name>[A-Za-z0-9_$-]+(?:\.[A-Za-z0-9_$-]+){0,2})/(?P<rest>.*)",
                       re.DOTALL)
_SEGMENT = re.compile(r"[A-Za-z0-9_.=-]+")


@dataclass(frozen=True, slots=True)
class RepositoryLocation:
    """The location slot of ``CREATE REPOSITORY``. Holds no location or secret."""

    kind: str
    path: str


@dataclass(frozen=True, slots=True)
class RepositoryTarget:
    clause: RepositoryLocation
    start: int
    end: int
    qualifier: tuple[str, ...]
    segments: tuple[str, ...]
    access: str


def is_backup_statement(parsed: ParsedStatement) -> bool:
    return type(parsed.statement_context).__name__ in BACKUP_CONTEXTS


def repository_targets(parsed: ParsedStatement) -> tuple[RepositoryTarget, ...]:
    """The stage location of ``CREATE REPOSITORY``, validated; ``()`` otherwise."""
    root = parsed.statement_context
    if type(root).__name__ != "CreateRepositoryStatementContext":
        return ()
    if root.brokerName is not None or root.properties() is not None:
        raise SemanticError(
            f"{FORM} on a stage takes no broker name or properties: the stage supplies the "
            "storage location and credentials. Write "
            "CREATE REPOSITORY <name> WITH BROKER ON LOCATION \"@<stage>/<folder>/\""
        )
    value = decode_literal(root.location.getText()).strip()
    if not value.startswith("@"):
        raise SemanticError(
            f"{FORM} keeps backups only in a stage. Write the location as "
            "\"@<stage>/<folder>/\", for example \"@backups/sales/\"; storage URLs are refused"
        )
    match = _LOCATION.fullmatch(value)
    rest = match.group("rest").rstrip("/") if match else ""
    if match is None or not rest:
        raise SemanticError(
            f"{FORM} needs a folder inside the stage, for example \"@backups/sales/\""
        )
    segments = tuple(rest.split("/"))
    for segment in segments:
        if not _SEGMENT.fullmatch(segment) or segment in {".", ".."}:
            raise SemanticError(
                f"{FORM}: the stage folder may use letters, digits, '_', '-', '.' and '=' "
                "between slashes, with no empty, '.' or '..' folders"
            )
    access = "read" if root.READ() is not None else "write"
    return (
        RepositoryTarget(
            RepositoryLocation("repository", value),
            root.location.start.start,
            root.location.stop.stop + 1,
            tuple(match.group("name").split(".")),
            segments,
            access,
        ),
    )


def physical_repository_location(bucket: str, base_prefix: str, path_parts: list[str]) -> str:
    """``s3a://bucket/<stage root>/<folders>`` (the engine's repository scheme)."""
    parts = [base_prefix.strip("/"), *path_parts]
    return f"s3a://{bucket}/" + "/".join(part for part in parts if part)


def render_repository_location(physical: str, credentials: list[str]) -> str:
    """The location literal and the credentials. Carries secrets: execution only."""
    literal = "'" + physical.replace("\\", "\\\\").replace("'", "''") + "'"
    return f"{literal} PROPERTIES({', '.join(credentials)})"


def require_backup_admin(statement: Any, role: str | None) -> None:
    """Refuse repository, backup and restore statements unless the role is ACCOUNTADMIN."""
    from app.core.exceptions import ForbiddenSQLError

    parsed = getattr(statement, "parsed", None)
    if parsed is None or not is_backup_statement(parsed):
        return
    if role != ADMIN_ROLE:
        raise ForbiddenSQLError(
            "Backup repositories, backups and restores need the ACCOUNTADMIN role: "
            "a repository keeps the stage's storage credential in the engine. "
            "Switch to ACCOUNTADMIN to run this statement."
        )
