"""One-shot operator commands that freeze, publish and reopen Nova control metadata.

Run outside application processes with the migrator login
(`NOVA_METADATA_MIGRATOR_DSN`), never from startup:

    python -m app.core.metadata_cutover status  --app-version SHA --operator NAME
    python -m app.core.metadata_cutover freeze  --app-version SHA --operator NAME --reason TEXT
    python -m app.core.metadata_cutover publish-epoch --manifest PATH ...
    python -m app.core.metadata_cutover unfreeze ...

Every mutation takes the exclusive deployment lock, so it waits for admitted
application transactions (which hold the shared lock) and is bounded by the
lock timeout. The decision, its journal row and the deployment-state change
commit together; a refusal commits only its journal row. A lost commit is
reported as an unknown outcome with the operation ID to verify with `status`;
nothing is retried automatically.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from app.core.metadata_db import (
    METADATA_DEPLOYMENT_LOCK,
    MetadataOperationFailed,
    MetadataOutcomeUnknown,
    MetadataPermissionDenied,
    MetadataPoolConfig,
    MetadataSchemaMismatch,
    MetadataTimeout,
)
from app.core.metadata_import import ImportContractError
from app.core.metadata_import_contract import ImportContract, load_contract
from app.core.metadata_import_manifest import (
    VALIDATION_FORMAT,
    ValidatedManifestRefused,
    canonical_json,
    verify_validated_document,
)
from app.core.metadata_import_semantics import CHECKS
from app.core.metadata_schema import (
    SchemaManifestError,
    load_migrations,
    verify_applied_migrations,
)

MANIFEST_FORMAT = VALIDATION_FORMAT
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
OPERATOR_PATTERN = re.compile(r"[A-Za-z0-9._@-]{1,128}")
APP_VERSION_PATTERN = re.compile(r"[0-9a-f]{40}")
TABLE_PATTERN = re.compile(r"[a-z_][a-z0-9_]{0,62}")


class CutoverConfigurationError(ValueError):
    def __init__(self) -> None:
        super().__init__("Invalid cutover command configuration")


@dataclass(frozen=True, slots=True)
class OperatorRequest:
    operator: str
    reason: str
    app_version: str

    def __post_init__(self) -> None:
        if (
            not OPERATOR_PATTERN.fullmatch(self.operator)
            or not APP_VERSION_PATTERN.fullmatch(self.app_version)
            or not 1 <= len(self.reason) <= 500
            or not self.reason.strip()
            or any(ord(char) < 32 and char not in "\t" for char in self.reason)
        ):
            raise CutoverConfigurationError()


@dataclass(frozen=True, slots=True)
class DeploymentState:
    schema_version: int
    metadata_epoch: UUID | None
    maintenance: bool
    active_freeze_id: UUID | None
    active_freeze_at: datetime | None


@dataclass(frozen=True, slots=True)
class Decision:
    refusal: str | None
    epoch_after: UUID | None = None
    maintenance_after: bool = True
    freeze_operation_id: UUID | None = None
    manifest_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class OperationResult:
    operation_id: UUID
    operation: str
    committed: bool
    refusal: str | None
    state: dict[str, Any] = field(default_factory=dict)


def _manifest_refusal(
    document: bytes,
    state: DeploymentState,
    expected_sha256: str,
    contract: ImportContract,
) -> str | None:
    """Require the whole reviewed import, bound to this freeze and schema ledger."""
    try:
        manifest = verify_validated_document(
            document,
            expected_sha256,
            schema_version=state.schema_version,
            ledger_rows=((m.version, m.name, m.checksum) for m in load_migrations()),
        )
    except ValidatedManifestRefused as error:
        return "manifest_" + error.reason
    if manifest.get("contract_sha256") != contract.sha256:
        return "manifest_contract_mismatch"
    semantics = manifest.get("semantics")
    if semantics != {"status": "VERIFIED", "checks": dict.fromkeys(CHECKS, 0)}:
        return "manifest_semantics_unverified"
    payloads = manifest.get("managed_payloads")
    if (
        not isinstance(payloads, dict)
        or payloads.get("status") != "VERIFIED"
        or not isinstance(payloads.get("receipts"), list)
        or type(payloads.get("count")) is not int
        or payloads["count"] != len(payloads["receipts"])
        or payloads.get("receipts_sha256")
        != hashlib.sha256(canonical_json(payloads["receipts"])).hexdigest()
    ):
        return "manifest_payloads_unverified"
    try:
        freeze_id = UUID(str(manifest.get("freeze_operation_id")))
        validated_at = datetime.fromisoformat(str(manifest.get("validated_at")))
    except ValueError:
        return "manifest_freeze_mismatch"
    if freeze_id != state.active_freeze_id:
        return "manifest_freeze_mismatch"
    if (
        validated_at.tzinfo is None
        or state.active_freeze_at is None
        or validated_at < state.active_freeze_at
    ):
        return "manifest_predates_freeze"
    tables = manifest["tables"]
    if set(tables) != set(contract.tables):
        return "manifest_incomplete_inventory"
    for name, table in contract.tables.items():
        entry = tables[name]
        if entry.get("target_table") != table.target_table:
            return "manifest_target_mismatch"
        if entry["status"] != ("MATCHED" if table.source_present else "ABSENT"):
            return "manifest_source_presence"
        source_count, target_count = entry.get("source_count"), entry.get("target_count")
        if (
            type(source_count) is not int
            or type(target_count) is not int
            or source_count < 0
            or source_count != target_count
            or (not table.source_present and source_count != 0)
        ):
            return "manifest_count_mismatch"
        for dimension in ("key_digest", "row_digest"):
            source, target = entry.get("source_" + dimension), entry.get("target_" + dimension)
            if (
                not isinstance(source, str)
                or not re.fullmatch(r"[0-9a-f]{64}", source)
                or source != target
            ):
                return "manifest_" + dimension + "_mismatch"
    return None


def decide_freeze(state: DeploymentState) -> Decision:
    if state.active_freeze_id is not None:
        return Decision("already_frozen", state.metadata_epoch, state.maintenance)
    return Decision(None, state.metadata_epoch, True)


def decide_unfreeze(state: DeploymentState) -> Decision:
    if state.metadata_epoch is None:
        return Decision("epoch_not_published", None, state.maintenance)
    if not state.maintenance:
        return Decision("not_frozen", state.metadata_epoch, False)
    return Decision(None, state.metadata_epoch, False)


def decide_publish(
    state: DeploymentState,
    manifest: bytes,
    expected_sha256: str,
    *,
    contract: ImportContract | None = None,
) -> Decision:
    unchanged = (state.metadata_epoch, state.maintenance)
    if state.metadata_epoch is not None:
        return Decision("epoch_already_published", *unchanged)
    if not state.maintenance or state.active_freeze_id is None:
        return Decision("not_frozen", *unchanged)
    if contract is None:
        try:
            contract = load_contract()
        except ImportContractError:
            return Decision("import_contract_unavailable", *unchanged)
    refusal = _manifest_refusal(manifest, state, expected_sha256, contract)
    if refusal:
        return Decision(refusal, *unchanged)
    return Decision(
        None,
        uuid4(),
        True,
        state.active_freeze_id,
        hashlib.sha256(manifest).hexdigest(),
    )


async def _connect(config: MetadataPoolConfig) -> psycopg.AsyncConnection[dict[str, Any]]:
    return await psycopg.AsyncConnection.connect(
        config.dsn,
        autocommit=True,
        row_factory=dict_row,
        prepare_threshold=None,
        application_name="nova-metadata-cutover",
        connect_timeout=max(1, int(config.acquire_timeout)),
        options=(
            f"-c statement_timeout={config.statement_timeout_ms} "
            f"-c lock_timeout={config.lock_timeout_ms} "
            f"-c idle_in_transaction_session_timeout={config.idle_transaction_timeout_ms} "
            "-c synchronous_commit=on -c timezone=UTC"
        ),
    )


async def _admit(conn: psycopg.AsyncConnection[dict[str, Any]]) -> None:
    """Act only as the migrator, through the owner role, on the packaged schema."""
    if conn.info.server_version // 10000 != 17:
        raise MetadataSchemaMismatch()
    cursor = await conn.execute(
        "SELECT session_user = 'nova_meta_migrator' AND "
        "pg_has_role(session_user, 'nova_meta_owner', 'SET') AS allowed"
    )
    identity = await cursor.fetchone()
    if not identity or not identity["allowed"]:
        raise MetadataPermissionDenied()
    await conn.execute("SET LOCAL ROLE nova_meta_owner")
    await conn.execute("SET LOCAL search_path = pg_catalog")


async def _read_state(
    conn: psycopg.AsyncConnection[dict[str, Any]],
    *,
    lock: bool,
) -> DeploymentState:
    cursor = await conn.execute(
        "SELECT version, name, checksum FROM nova_meta.schema_migrations ORDER BY version"
    )
    applied = await cursor.fetchall()
    try:
        installed = verify_applied_migrations(load_migrations(), applied)
    except SchemaManifestError:
        raise MetadataSchemaMismatch() from None
    cursor = await conn.execute(
        "SELECT schema_version, metadata_epoch, maintenance FROM nova_meta.deployment_state "
        "WHERE singleton = TRUE" + (" FOR UPDATE" if lock else "")
    )
    state = await cursor.fetchone()
    if not state or state["schema_version"] != installed or installed != len(load_migrations()):
        # This command only acts on the schema revision it was released with.
        raise MetadataSchemaMismatch()
    cursor = await conn.execute(
        "SELECT f.operation_id, f.recorded_at FROM nova_meta.cutover_operations f "
        "WHERE f.operation = 'freeze' AND f.outcome = 'committed' "
        "AND NOT EXISTS (SELECT 1 FROM nova_meta.cutover_operations u "
        "WHERE u.operation = 'unfreeze' AND u.outcome = 'committed' "
        "AND u.sequence > f.sequence) "
        "ORDER BY f.sequence DESC LIMIT 1"
    )
    freeze = await cursor.fetchone()
    return DeploymentState(
        int(state["schema_version"]),
        state["metadata_epoch"],
        bool(state["maintenance"]),
        freeze["operation_id"] if freeze else None,
        freeze["recorded_at"] if freeze else None,
    )


def _translate(error: psycopg.Error) -> Exception:
    if isinstance(error, (psycopg.errors.QueryCanceled, psycopg.errors.LockNotAvailable)):
        return MetadataTimeout()
    if isinstance(error, psycopg.errors.InsufficientPrivilege):
        return MetadataPermissionDenied()
    if isinstance(error, psycopg.OperationalError):
        return MetadataOutcomeUnknown()
    return MetadataOperationFailed()


async def execute(
    config: MetadataPoolConfig,
    operation: str,
    request: OperatorRequest,
    decide: Callable[[DeploymentState], Decision],
    *,
    operation_id: UUID | None = None,
) -> OperationResult:
    """Record and apply one operator decision under the exclusive deployment lock."""
    if operation not in {"freeze", "publish_epoch", "unfreeze"}:
        raise CutoverConfigurationError()
    operation_id = operation_id or uuid4()
    try:
        async with await _connect(config) as conn, conn.transaction():
            await _admit(conn)
            # Waits for admitted application transactions; bounded by lock_timeout.
            await conn.execute(
                "SELECT pg_advisory_xact_lock(%s)",
                (METADATA_DEPLOYMENT_LOCK,),
            )
            state = await _read_state(conn, lock=True)
            decision = decide(state)
            if operation == "publish_epoch" and decision.refusal is None:
                cursor = await conn.execute(
                    "SELECT count(*) AS n FROM nova_meta.metadata_import_runs "
                    "WHERE status='VALIDATED' AND validation_sha256=%s "
                    "AND freeze_operation_id=%s AND schema_version=%s "
                    "AND encode(sha256(validation_document),'hex')=validation_sha256",
                    (decision.manifest_sha256, state.active_freeze_id, state.schema_version),
                )
                receipt = await cursor.fetchone()
                if not receipt or receipt["n"] != 1:
                    decision = Decision(
                        "import_receipt_missing", state.metadata_epoch, state.maintenance
                    )
            await conn.execute(
                "INSERT INTO nova_meta.cutover_operations (operation_id, operation, "
                "outcome, refusal, operator, reason, app_version, schema_version, "
                "epoch_before, epoch_after, maintenance_before, maintenance_after, "
                "freeze_operation_id, manifest_sha256) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    operation_id,
                    operation,
                    "refused" if decision.refusal else "committed",
                    decision.refusal,
                    request.operator,
                    request.reason,
                    request.app_version,
                    state.schema_version,
                    state.metadata_epoch,
                    decision.epoch_after,
                    state.maintenance,
                    decision.maintenance_after,
                    decision.freeze_operation_id,
                    decision.manifest_sha256,
                ),
            )
            if decision.refusal is None:
                await conn.execute(
                    "UPDATE nova_meta.deployment_state SET metadata_epoch = %s, "
                    "maintenance = %s, updated_at = clock_timestamp() "
                    "WHERE singleton = TRUE",
                    (decision.epoch_after, decision.maintenance_after),
                )
        return OperationResult(
            operation_id,
            operation,
            decision.refusal is None,
            decision.refusal,
            {
                "schema_version": state.schema_version,
                "metadata_epoch": str(decision.epoch_after) if decision.epoch_after else None,
                "maintenance": decision.maintenance_after,
            },
        )
    except psycopg.Error as error:
        raise _translate(error) from None


async def status(config: MetadataPoolConfig, *, limit: int = 10) -> dict[str, Any]:
    """Read deployment state and the latest journal rows without changing anything."""
    try:
        async with await _connect(config) as conn, conn.transaction():
            await conn.execute("SET TRANSACTION READ ONLY")
            await _admit(conn)
            state = await _read_state(conn, lock=False)
            cursor = await conn.execute(
                "SELECT operation_id, operation, outcome, refusal, operator, "
                "epoch_after, maintenance_after, manifest_sha256, recorded_at "
                "FROM nova_meta.cutover_operations ORDER BY sequence DESC LIMIT %s",
                (limit,),
            )
            rows = await cursor.fetchall()
    except psycopg.Error as error:
        raise _translate(error) from None
    return {
        "schema_version": state.schema_version,
        "metadata_epoch": str(state.metadata_epoch) if state.metadata_epoch else None,
        "maintenance": state.maintenance,
        "active_freeze_operation_id": (
            str(state.active_freeze_id) if state.active_freeze_id else None
        ),
        "operations": [
            {
                "operation_id": str(row["operation_id"]),
                "operation": row["operation"],
                "outcome": row["outcome"],
                "refusal": row["refusal"],
                "operator": row["operator"],
                "metadata_epoch": str(row["epoch_after"]) if row["epoch_after"] else None,
                "maintenance": row["maintenance_after"],
                "manifest_sha256": row["manifest_sha256"],
                "recorded_at": row["recorded_at"].isoformat(),
            }
            for row in rows
        ],
    }


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True), flush=True)


def main(argv: list[str] | None = None) -> int:
    from pydantic import ValidationError

    from app.core.config import MetadataMigratorSettings

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "command",
        choices=("status", "freeze", "publish-epoch", "unfreeze"),
    )
    parser.add_argument("--app-version", required=True)
    parser.add_argument("--operator", required=True)
    parser.add_argument("--reason", default="status")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--manifest-sha256")
    args = parser.parse_args(argv)
    try:
        request = OperatorRequest(args.operator, args.reason, args.app_version)
        publish = args.command == "publish-epoch"
        if publish != (args.manifest is not None) or publish != (args.manifest_sha256 is not None):
            raise CutoverConfigurationError()
        manifest = b""
        if args.manifest is not None:
            with args.manifest.open("rb") as handle:
                manifest = handle.read(MAX_MANIFEST_BYTES + 1)
        settings = MetadataMigratorSettings()  # type: ignore[call-arg]
        config = MetadataPoolConfig(
            settings.dsn.get_secret_value(),
            statement_timeout_ms=settings.statement_timeout_ms,
            lock_timeout_ms=settings.lock_timeout_ms,
        )
    except (ValidationError, ValueError, OSError):
        _print({"status": "REFUSED", "error": "invalid_cutover_configuration"})
        return 2
    operation_id = uuid4()
    try:
        if args.command == "status":
            _print({"status": "STATUS", **asyncio.run(status(config))})
            return 0
        operation = args.command.replace("-", "_")
        decide: Callable[[DeploymentState], Decision] = {
            "freeze": decide_freeze,
            "unfreeze": decide_unfreeze,
            "publish_epoch": lambda state: decide_publish(state, manifest, args.manifest_sha256),
        }[operation]
        # Printed before connecting so an unknown outcome can be verified by ID.
        _print({"status": "INTENT", "operation": operation, "operation_id": str(operation_id)})
        result = asyncio.run(
            execute(
                config,
                operation,
                request,
                decide,
                operation_id=operation_id,
            )
        )
    except MetadataOutcomeUnknown:
        _print(
            {
                "status": "OUTCOME_UNKNOWN",
                "operation_id": str(operation_id),
                "next": "run status and look for this operation_id before any retry",
            }
        )
        return 1
    except (
        MetadataOperationFailed,
        MetadataPermissionDenied,
        MetadataSchemaMismatch,
        MetadataTimeout,
    ) as error:
        _print({"status": "REFUSED", "error": type(error).__name__})
        return 1
    _print(
        {
            "status": "COMMITTED" if result.committed else "REFUSED",
            "operation": result.operation,
            "operation_id": str(result.operation_id),
            "refusal": result.refusal,
            **result.state,
        }
    )
    return 0 if result.committed else 1


if __name__ == "__main__":
    sys.exit(main())
