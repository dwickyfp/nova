"""ML engine control metadata in the caller's PostgreSQL transaction.

The prepared replacement for the ``NOVA_SYSTEM`` paths of
:class:`MLJobRepository`, :class:`ModelRegistryRepository`,
:class:`EphemeralRepository` and the registry SQL in :class:`MLEngineService`,
switched in at the all-domain cutover. Model and result bytes stay in the
artifact store; these rows only name them. Compared with the source it:

* claims a training job under a new lease generation, so a worker that lost
  its claim can no longer heartbeat or record an outcome;
* creates a job and a run record once by id, never resetting a raced row;
* reserves a model version under a row lock on the owner's model, so
  concurrent reservations get distinct versions instead of relying on a Redis
  lock, and changes the model row only at the revision it read;
* publishes a version once (TRAINING to READY or FAILED); a published or
  failed version is provenance and never changes again;
* moves an alias only from the revision the caller read.

Encoding of descriptors and telemetry stays with the callers.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.pq import TransactionStatus

from app.common.responses import SanitizingJSONResponse
from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied
from app.modules.access_control.security_context import SecurityContext

#: Every job column except the encrypted SQL, which only the worker reads.
_JOB_COLUMNS = (
    "id,actor,session_fingerprint,active_role,security_context_version,tenant,request_json,"
    "status,cancel_requested,result_json,error_kind,error_message,created_at,started_at,"
    "heartbeat_at,finished_at,lease_generation,provenance"
)
TERMINAL_JOB = ("succeeded", "failed", "cancelled", "interrupted")
TERMINAL_RUN = ("COMPLETE", "FAILED", "succeeded", "failed", "cancelled")
_MODEL_DEFINITION = ("model_type", "target_column", "feature_columns", "hyperparameters",
                     "training_sql")
_PUBLISHED = ("training_rows", "metrics", "artifact_uri", "artifact_sha256", "artifact_size",
              "task", "framework", "framework_version", "algorithm", "feature_schema",
              "training_duration_ms")
ABORT_REASON = "artifact registration did not complete"


def _generation(value: int) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("A positive lease generation is required")
    return value


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value, default=str)


def alias_scope(database_name: str | None, tenant: str) -> str:
    """The alias database scope the source registry uses."""
    from app.modules.ml_engine.registry.repository import alias_database_scope

    return alias_database_scope(database_name, tenant)


class MLMetadataRepository:
    def __init__(
        self, connection: psycopg.AsyncConnection[dict[str, Any]], actor: SecurityContext,
    ) -> None:
        self.connection = connection
        self.actor = actor

    async def _bound(self) -> None:
        if self.connection.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError("ML metadata requires an active metadata transaction")
        cursor = await self.connection.execute(
            "SELECT current_setting('nova.principal',true)=%s "
            "AND current_setting('nova.active_role',true)=%s "
            "AND current_setting('nova.security_context_version',true)=%s "
            "AND current_setting('nova.service_identity',true)=current_user AS valid",
            (self.actor.principal, self.actor.active_role,
             str(self.actor.security_context_version)),
        )
        row = await cursor.fetchone()
        if not row or row["valid"] is not True:
            raise MetadataPermissionDenied()

    async def _one(self, query: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        cursor = await self.connection.execute(query, params)
        return await cursor.fetchone()

    async def _all(self, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        cursor = await self.connection.execute(query, params)
        return list(await cursor.fetchall())

    # ── Training jobs ─────────────────────────────────────────────────────

    async def create_job(
        self, *, job_id: str, fingerprint: str, tenant: str, request: dict[str, Any],
        encrypted_sql: str,
    ) -> tuple[dict[str, Any], bool]:
        """Queue a job for the bound actor once by id; a repeat returns the existing job."""
        await self._bound()
        row = await self._one(
            "INSERT INTO nova_meta.config_ml_jobs (id,actor,session_fingerprint,active_role,"
            "security_context_version,tenant,request_json,encrypted_sql,status) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'queued') ON CONFLICT (id) DO NOTHING "
            f"RETURNING {_JOB_COLUMNS}",
            (job_id, self.actor.principal, fingerprint, self.actor.active_role,
             self.actor.security_context_version, tenant, json.dumps(request), encrypted_sql),
        )
        if row is not None:
            return row, True
        existing = await self.get_job(job_id)
        if existing is None or existing["actor"] != self.actor.principal:
            raise MetadataConflict()
        return existing, False

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            f"SELECT {_JOB_COLUMNS} FROM nova_meta.config_ml_jobs WHERE id=%s", (job_id,),
        )

    async def get_job_for_worker(self, job_id: str) -> dict[str, Any] | None:
        """The job with its encrypted SQL; only the ML worker may read it."""
        await self._bound()
        return await self._one(
            f"SELECT {_JOB_COLUMNS},encrypted_sql FROM nova_meta.config_ml_jobs WHERE id=%s",
            (job_id,),
        )

    async def queued_jobs(self, limit: int) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            f"SELECT {_JOB_COLUMNS} FROM nova_meta.config_ml_jobs WHERE status='queued' "
            "AND NOT cancel_requested ORDER BY created_at,id LIMIT %s", (limit,),
        )

    async def claim_job(self, job_id: str) -> int | None:
        """Start a queued job under a new lease; returns its generation, or None if lost."""
        await self._bound()
        row = await self._one(
            "UPDATE nova_meta.config_ml_jobs SET status='running',"
            "lease_generation=lease_generation+1 WHERE id=%s AND status='queued' "
            "AND NOT cancel_requested RETURNING lease_generation", (job_id,),
        )
        return int(row["lease_generation"]) if row else None

    async def heartbeat_job(self, job_id: str, *, generation: int) -> tuple[bool, bool]:
        """Record liveness for the current lease. Returns (still held, cancel requested)."""
        await self._bound()
        row = await self._one(
            "UPDATE nova_meta.config_ml_jobs SET heartbeat_at=clock_timestamp() "
            "WHERE id=%s AND status='running' AND lease_generation=%s "
            "RETURNING cancel_requested", (job_id, _generation(generation)),
        )
        if row is None:
            return False, False
        return True, bool(row["cancel_requested"])

    async def request_cancel(self, job_id: str) -> bool:
        await self._bound()
        row = await self._one(
            "UPDATE nova_meta.config_ml_jobs SET cancel_requested=true WHERE id=%s "
            "AND status IN ('queued','running') AND NOT cancel_requested RETURNING id",
            (job_id,),
        )
        return row is not None

    async def withdraw_queued_job(
        self, job_id: str, *, error_kind: str, error_message: str,
    ) -> bool:
        """Fail a job no worker claimed, so a late worker does not train it."""
        await self._bound()
        row = await self._one(
            "UPDATE nova_meta.config_ml_jobs SET status='failed',error_kind=%s,error_message=%s "
            "WHERE id=%s AND status='queued' RETURNING id",
            (error_kind, error_message, job_id),
        )
        return row is not None

    async def finish_job(
        self, job_id: str, status: str, *, generation: int,
        result: dict[str, Any] | None = None, error_kind: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        """Record the outcome, only for the worker holding the current lease."""
        if status not in TERMINAL_JOB:
            raise ValueError("ML job must finish in a terminal state")
        await self._bound()
        safe = SanitizingJSONResponse._sanitize(result) if result is not None else None
        row = await self._one(
            "UPDATE nova_meta.config_ml_jobs SET status=%s,result_json=%s,error_kind=%s,"
            "error_message=%s WHERE id=%s AND status='running' AND lease_generation=%s "
            "RETURNING id",
            (status, _json(safe), error_kind, error_message, job_id, _generation(generation)),
        )
        return row is not None

    async def interrupt_stale_jobs(self, *, older_than_seconds: int = 120) -> list[str]:
        await self._bound()
        rows = await self._all(
            "UPDATE nova_meta.config_ml_jobs SET status='interrupted',"
            "error_kind='worker_interrupted' WHERE status='running' "
            "AND heartbeat_at < clock_timestamp() - make_interval(secs => %s) RETURNING id",
            (float(older_than_seconds),),
        )
        return [row["id"] for row in rows]

    async def prune_finished_jobs(self) -> int:
        """Delete jobs finished more than a day ago; interrupted jobs are kept."""
        await self._bound()
        cursor = await self.connection.execute(
            "DELETE FROM nova_meta.config_ml_jobs WHERE status IN ('succeeded','failed',"
            "'cancelled') AND finished_at < clock_timestamp() - interval '1 day'",
        )
        return cursor.rowcount

    # ── Model registry ────────────────────────────────────────────────────

    async def _owned_model(
        self, model_name: str, tenant: str, database: str | None, schema: str | None,
    ) -> dict[str, Any] | None:
        return await self._one(
            "SELECT * FROM nova_meta.ml_models WHERE model_name=%s AND created_by=%s "
            "AND tenant_name=%s AND coalesce(database_name,'')=coalesce(%s,'') "
            "AND coalesce(schema_name,'')=coalesce(%s,'') "
            "ORDER BY updated_at DESC,model_id LIMIT 1 FOR UPDATE",
            (model_name, self.actor.principal, tenant, database, schema),
        )

    async def reserve_version(
        self, *, model_name: str, model_type: str, tenant: str, database: str | None,
        schema: str | None, target_column: str | None, feature_columns: list[str],
        hyperparameters: dict[str, Any], training_sql: str,
        reservation_token: str | None = None,
    ) -> dict[str, Any]:
        """Reserve the next TRAINING version of the bound actor's model.

        ``training_sql`` must already be redacted. Returns ``model_id``,
        ``version`` and the ``reservation_token`` that publishing needs. A
        queued training job passes its durable operation id as the token, so
        the participant verifier can find the version that attempt reserved.
        """
        await self._bound()
        if reservation_token is not None and (
            not isinstance(reservation_token, str) or not reservation_token.strip()
            or len(reservation_token) > 64
        ):
            raise ValueError("Invalid reservation token")
        definition = {
            "model_type": model_type, "target_column": target_column,
            "feature_columns": json.dumps(feature_columns),
            "hyperparameters": json.dumps(hyperparameters, default=str),
            "training_sql": training_sql,
        }
        model = await self._owned_model(model_name, tenant, database, schema)
        created = None
        if model is None:
            created = await self._one(
                "INSERT INTO nova_meta.ml_models (model_id,model_name,database_name,schema_name,"
                "created_by,tenant_name," + ",".join(_MODEL_DEFINITION) + ") "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING "
                "RETURNING model_id,current_version",
                (str(uuid4()), model_name, database, schema, self.actor.principal, tenant,
                 *definition.values()),
            )
            if created is None:
                # A concurrent reservation created it first; take its lock and change it.
                model = await self._owned_model(model_name, tenant, database, schema)
                if model is None:
                    raise MetadataConflict()
        if created is not None:
            model = created
        else:
            assert model is not None
            # The row is locked: the revision read is the revision changed.
            changed = await self._one(
                "UPDATE nova_meta.ml_models SET "
                + ",".join(f"{field}=%s" for field in _MODEL_DEFINITION)
                + ",metadata_revision=metadata_revision+1 WHERE model_id=%s "
                "AND metadata_revision=%s RETURNING model_id",
                (*definition.values(), model["model_id"], model["metadata_revision"]),
            )
            if changed is None:
                raise MetadataConflict()
        latest = await self._one(
            "SELECT coalesce(max(version),0) AS version FROM nova_meta.ml_model_versions "
            "WHERE model_id=%s", (model["model_id"],),
        )
        version = max(int(latest["version"] if latest else 0),
                      int(model["current_version"] or 0)) + 1
        token = reservation_token or str(uuid4())
        await self.connection.execute(
            "INSERT INTO nova_meta.ml_model_versions (model_id,version,status,reservation_token,"
            "created_by) VALUES (%s,%s,'TRAINING',%s,%s)",
            (model["model_id"], version, token, self.actor.principal),
        )
        return {"model_id": model["model_id"], "version": version, "reservation_token": token}

    async def publish_version(
        self, *, model_id: str, version: int, reservation_token: str,
        feature_columns: list[str], fields: dict[str, Any],
    ) -> dict[str, Any]:
        """Publish a reserved version once and advance the model's current version."""
        await self._bound()
        unknown = set(fields) - set(_PUBLISHED)
        if unknown:
            raise ValueError(f"Unsupported version fields: {sorted(unknown)}")
        values = {name: fields.get(name) for name in _PUBLISHED}
        for name in ("metrics", "feature_schema"):
            if values[name] is not None and not isinstance(values[name], str):
                values[name] = json.dumps(values[name], default=str)
        model = await self._one(
            "SELECT model_id,metadata_revision,current_version FROM nova_meta.ml_models "
            "WHERE model_id=%s FOR UPDATE", (model_id,),
        )
        if model is None:
            raise MetadataConflict()
        published = await self._one(
            "UPDATE nova_meta.ml_model_versions SET status='READY',"
            + ",".join(f"{name}=%s" for name in _PUBLISHED)
            + " WHERE model_id=%s AND version=%s AND status='TRAINING' "
            "AND reservation_token=%s RETURNING *",
            (*values.values(), model_id, version, reservation_token),
        )
        if published is None:
            raise MetadataConflict()
        current = max(int(model["current_version"] or 0), version)
        changed = await self._one(
            "UPDATE nova_meta.ml_models SET feature_columns=%s,current_version=%s,"
            "metadata_revision=metadata_revision+1 WHERE model_id=%s AND metadata_revision=%s "
            "RETURNING model_id",
            (json.dumps(feature_columns), current, model_id, model["metadata_revision"]),
        )
        if changed is None:
            raise MetadataConflict()
        return published

    async def abort_version(self, model_id: str, version: int) -> bool:
        """Fail an unpublished reservation; False when the version is already published."""
        await self._bound()
        failed = await self._one(
            "UPDATE nova_meta.ml_model_versions SET status='FAILED',failure_reason=%s "
            "WHERE model_id=%s AND version=%s AND status='TRAINING' RETURNING version",
            (ABORT_REASON, model_id, version),
        )
        if failed is not None:
            return True
        # Not training (any more): a concurrent publish wins, anything else is gone.
        row = await self._one(
            "SELECT status FROM nova_meta.ml_model_versions WHERE model_id=%s AND version=%s",
            (model_id, version),
        )
        return row is None or row["status"] != "READY"

    async def version_by_reservation(self, reservation_token: str) -> dict[str, Any] | None:
        """The version a reservation token named, published or not (verifier lookup)."""
        await self._bound()
        return await self._one(
            "SELECT model_id,version,status,artifact_uri,artifact_sha256,artifact_size,"
            "created_by FROM nova_meta.ml_model_versions WHERE reservation_token=%s "
            "ORDER BY model_id,version LIMIT 1", (reservation_token,),
        )

    async def get_version(
        self, model_id: str, version: int, *, owner_name: str, database_name: str | None,
        tenant: str = "default",
    ) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT v.*,m.model_name,m.model_type,m.tenant_name "
            "FROM nova_meta.ml_model_versions v JOIN nova_meta.ml_models m "
            "ON m.model_id=v.model_id WHERE v.model_id=%s AND v.version=%s "
            "AND m.created_by=%s AND coalesce(m.database_name,'')=coalesce(%s,'') "
            "AND m.tenant_name=%s AND v.status='READY'",
            (model_id, version, owner_name, database_name, tenant),
        )

    async def list_models(
        self, *, owner_name: str, database_name: str | None = None, tenant: str = "default",
    ) -> list[dict[str, Any]]:
        await self._bound()
        scoped = database_name is not None
        return await self._all(
            "SELECT m.*,v.version AS latest_version,v.status AS latest_status,"
            "v.metrics AS latest_metrics,v.training_rows,v.algorithm AS algorithm "
            "FROM nova_meta.ml_models m LEFT JOIN nova_meta.ml_model_versions v "
            "ON m.model_id=v.model_id AND v.version=m.current_version "
            "WHERE m.created_by=%s AND m.tenant_name=%s "
            + ("AND coalesce(m.database_name,'')=coalesce(%s,'') " if scoped else "")
            + "ORDER BY m.updated_at DESC,m.model_id",
            (owner_name, tenant, database_name) if scoped else (owner_name, tenant),
        )

    async def list_versions(self, model_id: str) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT version,status,training_rows,metrics,artifact_uri,artifact_size,framework,"
            "algorithm,created_at,created_by FROM nova_meta.ml_model_versions "
            "WHERE model_id=%s ORDER BY version DESC", (model_id,),
        )

    async def delete_model(self, model_id: str) -> bool:
        """Delete the bound actor's model with its aliases and versions.

        The caller removes the artifacts first, as the source does.
        """
        await self._bound()
        await self.connection.execute(
            "DELETE FROM nova_meta.ml_model_aliases WHERE model_id=%s", (model_id,),
        )
        await self.connection.execute(
            "DELETE FROM nova_meta.ml_model_versions WHERE model_id=%s", (model_id,),
        )
        cursor = await self.connection.execute(
            "DELETE FROM nova_meta.ml_models WHERE model_id=%s", (model_id,),
        )
        return cursor.rowcount == 1

    # ── Aliases ───────────────────────────────────────────────────────────

    async def get_alias(
        self, alias: str, *, database_name: str | None, tenant: str = "default",
    ) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.ml_model_aliases WHERE alias_name=%s AND owner_name=%s "
            "AND database_name=%s",
            (alias, self.actor.principal, alias_scope(database_name, tenant)),
        )

    async def set_alias(
        self, alias: str, model_id: str, version: int, *, database_name: str | None,
        tenant: str = "default", expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Create the bound actor's alias, or move it from ``expected_revision``."""
        await self._bound()
        scope = alias_scope(database_name, tenant)
        if expected_revision is None:
            row = await self._one(
                "INSERT INTO nova_meta.ml_model_aliases (alias_name,owner_name,database_name,"
                "model_id,version) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING *",
                (alias, self.actor.principal, scope, model_id, version),
            )
        else:
            row = await self._one(
                "UPDATE nova_meta.ml_model_aliases SET model_id=%s,version=%s,"
                "metadata_revision=metadata_revision+1 WHERE alias_name=%s AND owner_name=%s "
                "AND database_name=%s AND metadata_revision=%s RETURNING *",
                (model_id, version, alias, self.actor.principal, scope,
                 _generation(expected_revision)),
            )
        if row is None:
            raise MetadataConflict()
        return row

    async def resolve_alias(
        self, alias: str, *, owner_name: str, database_name: str | None, tenant: str = "default",
    ) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT a.model_id,a.version,m.model_name,m.model_type,m.tenant_name,"
            "v.artifact_uri,v.artifact_sha256,v.artifact_size,v.model_binary "
            "FROM nova_meta.ml_model_aliases a "
            "JOIN nova_meta.ml_models m ON m.model_id=a.model_id "
            "JOIN nova_meta.ml_model_versions v ON v.model_id=a.model_id AND v.version=a.version "
            "WHERE a.alias_name=%s AND a.owner_name=%s AND a.database_name=%s "
            "AND m.tenant_name=%s AND v.status='READY'",
            (alias, owner_name, alias_scope(database_name, tenant), tenant),
        )

    async def list_aliases(
        self, *, owner_name: str, database_name: str | None = None, tenant: str = "default",
    ) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT a.alias_name,a.model_id,a.version,a.created_at,a.metadata_revision,"
            "m.model_name FROM nova_meta.ml_model_aliases a LEFT JOIN nova_meta.ml_models m "
            "ON m.model_id=a.model_id WHERE a.owner_name=%s AND a.database_name=%s "
            "ORDER BY a.alias_name",
            (owner_name, alias_scope(database_name, tenant)),
        )

    async def delete_alias(
        self, alias: str, *, database_name: str | None, tenant: str = "default",
    ) -> bool:
        await self._bound()
        cursor = await self.connection.execute(
            "DELETE FROM nova_meta.ml_model_aliases WHERE alias_name=%s AND owner_name=%s "
            "AND database_name=%s",
            (alias, self.actor.principal, alias_scope(database_name, tenant)),
        )
        return cursor.rowcount == 1

    # ── Ephemeral run descriptors ─────────────────────────────────────────

    async def put_ephemeral(
        self, *, run_id: str, scope_key: str, fingerprint: str, descriptor_json: str,
        expires_epoch: float,
    ) -> None:
        """Write or replace a descriptor within its scope; another scope's id conflicts."""
        await self._bound()
        row = await self._one(
            "INSERT INTO nova_meta.ml_ephemeral_runs (run_id,scope_key,fingerprint,"
            "descriptor_json,expires_epoch) VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT (run_id) DO UPDATE SET fingerprint=EXCLUDED.fingerprint,"
            "descriptor_json=EXCLUDED.descriptor_json,expires_epoch=EXCLUDED.expires_epoch "
            "WHERE ml_ephemeral_runs.scope_key=EXCLUDED.scope_key RETURNING run_id",
            (run_id, scope_key, fingerprint, descriptor_json, float(expires_epoch)),
        )
        if row is None:
            raise MetadataConflict()

    async def get_ephemeral(self, run_id: str, scope_key: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.ml_ephemeral_runs WHERE run_id=%s AND scope_key=%s "
            "AND expires_epoch>extract(epoch FROM clock_timestamp())", (run_id, scope_key),
        )

    async def ephemeral_by_fingerprint(
        self, fingerprint: str, scope_key: str,
    ) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.ml_ephemeral_runs WHERE fingerprint=%s AND scope_key=%s "
            "AND expires_epoch>extract(epoch FROM clock_timestamp()) "
            "ORDER BY expires_epoch DESC LIMIT 1", (fingerprint, scope_key),
        )

    async def expired_ephemeral(self, limit: int = 100) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.ml_ephemeral_runs "
            "WHERE expires_epoch<=extract(epoch FROM clock_timestamp()) "
            "ORDER BY expires_epoch LIMIT %s", (limit,),
        )

    async def delete_expired_ephemeral(self, run_id: str, scope_key: str) -> bool:
        await self._bound()
        cursor = await self.connection.execute(
            "DELETE FROM nova_meta.ml_ephemeral_runs WHERE run_id=%s AND scope_key=%s "
            "AND expires_epoch<=extract(epoch FROM clock_timestamp())", (run_id, scope_key),
        )
        return cursor.rowcount == 1

    async def remove_ephemeral(self, run_id: str, scope_key: str) -> bool:
        await self._bound()
        cursor = await self.connection.execute(
            "DELETE FROM nova_meta.ml_ephemeral_runs WHERE run_id=%s AND scope_key=%s",
            (run_id, scope_key),
        )
        return cursor.rowcount == 1

    # ── Run telemetry ─────────────────────────────────────────────────────

    async def record_run(self, data: dict[str, Any], *, ttl_seconds: float) -> dict[str, Any]:
        """Create the bound actor's run record once, or move a RUNNING record to its outcome."""
        await self._bound()
        values = (
            data.get("model_id"), data.get("model_version"), data.get("artifact_uri"),
            data.get("fingerprint"), _json(data.get("telemetry")), data.get("error_class"),
            data.get("error_message"), float(ttl_seconds),
        )
        row = await self._one(
            "INSERT INTO nova_meta.ml_runs (run_id,task,mode,status,owner_name,tenant_name,"
            "database_name,model_id,model_version,artifact_uri,fingerprint,telemetry,"
            "error_class,error_message,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
            "%s,%s,%s,clock_timestamp()+make_interval(secs => %s)) "
            "ON CONFLICT (run_id) DO NOTHING RETURNING *",
            (data["run_id"], data["task"], data["mode"], data["status"], self.actor.principal,
             data.get("tenant_name") or "default", data.get("database_name"), *values),
        )
        if row is not None:
            return row
        row = await self._one(
            "UPDATE nova_meta.ml_runs SET status=%s,model_id=coalesce(%s,model_id),"
            "model_version=coalesce(%s,model_version),artifact_uri=coalesce(%s,artifact_uri),"
            "fingerprint=coalesce(%s,fingerprint),telemetry=coalesce(%s,telemetry),"
            "error_class=%s,error_message=%s,"
            "expires_at=clock_timestamp()+make_interval(secs => %s) "
            "WHERE run_id=%s AND status='RUNNING' AND task=%s AND mode=%s RETURNING *",
            (data["status"], *values, data["run_id"], data["task"], data["mode"]),
        )
        if row is None:
            raise MetadataConflict()
        return row

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one("SELECT * FROM nova_meta.ml_runs WHERE run_id=%s", (run_id,))
