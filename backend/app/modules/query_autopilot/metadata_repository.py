"""Query Autopilot control metadata in the caller's PostgreSQL transaction.

The prepared replacement for the ``NOVA_SYSTEM`` paths of
:class:`AutopilotRepository`, switched in at the all-domain cutover. Payloads
and opaque ids are kept exactly as the source writes them; the store adds the
rules the source left to Redis locks and blind upserts:

* policies and enrollments change only from the version that was read (CAS),
  and a candidate changes only from the metadata revision that was read;
* an approval is bound in the store to the approving principal, active role
  and security-context version, cannot be restored or carried across a new
  candidate version, and applying needs a current approval (or ``READY_AUTO``);
* jobs are created once by id (an idempotency key reused for a different
  request is a conflict), and a claim advances a lease generation; only the
  current holder heartbeats, finishes, or writes the job's experiment, action
  and outcome, so a stale worker cannot apply or record an outcome;
* actions record their intent before the engine change and are acknowledged
  once, keeping the statements, binding and verified object identity; outcomes
  and observations are facts: an identical retry is accepted, a different one
  is refused.

PG cannot make an engine change and its acknowledgement atomic. An intent
without an acknowledgement stays ``APPLYING`` for reconciliation; the caller
never re-submits it. Query text never reaches this store: samples stay
encrypted in the payload store and evidence keeps only a reference and digest.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied
from app.modules.access_control.security_context import SecurityContext

TABLES = {
    kind: "query_autopilot_" + kind
    for kind in (
        "families", "observations", "rollups", "baselines", "evidence", "incidents",
        "opportunities", "experiments", "actions", "outcomes", "policies", "enrollments", "jobs",
    )
}
DEFINITIONS = ("policies", "enrollments")
RESULTS = ("experiments", "actions", "outcomes")
SUMMARIES = ("families", "rollups", "baselines")
RETAINED = ("observations", "rollups", "baselines")
TERMINAL_JOB = ("COMPLETED", "BLOCKED")
MAX_OBSERVATIONS = 128


def _payload(value: dict[str, Any]) -> dict[str, Any]:
    """The JSON the source stores (``default=str``, no NaN), as a plain object."""
    if not isinstance(value, dict):
        raise ValueError("An autopilot payload is a JSON object")
    loaded = json.loads(json.dumps(value, separators=(",", ":"), default=str, allow_nan=False))
    assert isinstance(loaded, dict)
    return loaded


def _table(kind: str, allowed: tuple[str, ...] | None = None) -> str:
    if kind not in TABLES or (allowed is not None and kind not in allowed):
        raise ValueError("Unsupported autopilot collection")
    return TABLES[kind]


def _positive(value: int, name: str = "version") -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"A positive {name} is required")
    return value


class AutopilotMetadataRepository:
    def __init__(
        self, connection: psycopg.AsyncConnection[dict[str, Any]], actor: SecurityContext,
    ) -> None:
        self.connection = connection
        self.actor = actor

    async def _bound(self) -> None:
        if self.connection.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError("Autopilot metadata requires an active metadata transaction")
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

    async def _created_once(
        self, kind: str, values: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        columns = list(values)
        row = await self._one(
            f"INSERT INTO nova_meta.{TABLES[kind]} ({','.join(columns)}) VALUES ("
            + ",".join(["%s"] * len(columns)) + ") ON CONFLICT (id) DO NOTHING RETURNING *",
            tuple(values.values()),
        )
        if row is not None:
            return row, True
        existing = await self._one(
            f"SELECT * FROM nova_meta.{TABLES[kind]} WHERE id=%s", (values["id"],),
        )
        if existing is None:  # pragma: no cover - a row hidden by RLS is a refusal
            raise MetadataPermissionDenied()
        return existing, False

    # ── Reads ─────────────────────────────────────────────────────────────

    async def get(self, kind: str, identifier: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            f"SELECT * FROM nova_meta.{_table(kind)} WHERE id=%s", (identifier,),
        )

    async def page(
        self,
        kind: str,
        *,
        after: str = "",
        limit: int = 100,
        family_id: str | None = None,
        cohort_id: str | None = None,
        state: str | None = None,
        since: datetime | None = None,
        created_since: datetime | None = None,
        before: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Rows after ``after`` in id order, filtered as the source repository filters."""
        await self._bound()
        clauses: list[str] = ["id>%s"]
        params: list[Any] = [after]
        for column, value in (("family_id", family_id), ("cohort_id", cohort_id),
                              ("state", state)):
            if value is not None:
                clauses.append(f"{column}=%s")
                params.append(value)
        for clause, moment in (("updated_at>=%s", since), ("created_at>=%s", created_since),
                               ("created_at<%s", before)):
            if moment is not None:
                clauses.append(clause)
                params.append(moment)
        params.append(max(1, min(int(limit), 1000)))
        cursor = await self.connection.execute(
            f"SELECT * FROM nova_meta.{_table(kind)} WHERE {' AND '.join(clauses)} "
            "ORDER BY id LIMIT %s", tuple(params),
        )
        return list(await cursor.fetchall())

    async def count(self, kind: str) -> int:
        """How many rows of a collection this workload can see (the overview counts)."""
        await self._bound()
        row = await self._one(f"SELECT count(*) AS n FROM nova_meta.{_table(kind)}", ())
        return int(row["n"]) if row else 0

    # ── Governed definitions ──────────────────────────────────────────────

    async def save_definition(
        self, kind: str, identifier: str, payload: dict[str, Any], *,
        expected_version: int | None, cohort_id: str = "",
    ) -> dict[str, Any]:
        """Create a policy/enrollment, or change it only from ``expected_version``.

        The payload carries the new version, as the source model does.
        """
        await self._bound()
        table = _table(kind, DEFINITIONS)
        data = _payload(payload)
        version = _positive(data.get("version"))  # type: ignore[arg-type]
        if expected_version is None:
            inserted, created = await self._created_once(kind, {
                "id": identifier, "family_id": "", "cohort_id": cohort_id,
                "version": version, "state": "", "payload": Jsonb(data),
            })
            if not created:
                raise MetadataConflict()
            return inserted
        row = await self._one(
            f"UPDATE nova_meta.{table} SET payload=%s,version=%s "
            "WHERE id=%s AND version=%s RETURNING *",
            (Jsonb(data), version, identifier, _positive(expected_version)),
        )
        if row is None:
            raise MetadataConflict()
        return row

    # ── Candidates ────────────────────────────────────────────────────────

    async def propose(
        self, identifier: str, payload: dict[str, Any], *, family_id: str, cohort_id: str,
    ) -> dict[str, Any]:
        """Persist a new, unapproved candidate; an existing id is a conflict."""
        await self._bound()
        data = _payload(payload)
        row, created = await self._created_once("opportunities", {
            "id": identifier, "family_id": family_id, "cohort_id": cohort_id,
            "version": data.get("version", 1), "state": data.get("state"),
            "payload": Jsonb(data),
        })
        if not created:
            raise MetadataConflict()
        return row

    async def update_candidate(
        self, identifier: str, payload: dict[str, Any], *, expected_revision: int,
        withdraw_approval: bool = False,
    ) -> dict[str, Any]:
        """Change a candidate only from the metadata revision that was read.

        The stored approval stays as it is unless ``withdraw_approval`` clears it;
        the store refuses to keep it across a new candidate version.
        """
        await self._bound()
        data = _payload(payload)
        clear = (",approved_by=NULL,approved_role=NULL,approved_context_version=NULL,"
                 "approval_digest=NULL,approval_expires_at=NULL") if withdraw_approval else ""
        row = await self._one(
            "UPDATE nova_meta.query_autopilot_opportunities SET payload=%s,state=%s,version=%s,"
            f"metadata_revision=metadata_revision+1{clear} "
            "WHERE id=%s AND metadata_revision=%s RETURNING *",
            (Jsonb(data), data.get("state"), data.get("version"), identifier,
             _positive(expected_revision, "revision")),
        )
        if row is None:
            raise MetadataConflict()
        return row

    async def approve(
        self, identifier: str, payload: dict[str, Any], *, expected_revision: int,
        approval_digest: str, expires_at: datetime,
    ) -> dict[str, Any]:
        """Record an approval; the store binds it to this transaction's actor."""
        await self._bound()
        data = _payload(payload)
        row = await self._one(
            "UPDATE nova_meta.query_autopilot_opportunities SET payload=%s,state='APPROVED',"
            "version=%s,metadata_revision=metadata_revision+1,approval_digest=%s,"
            "approval_expires_at=%s WHERE id=%s AND metadata_revision=%s RETURNING *",
            (Jsonb(data), data.get("version"), approval_digest, expires_at, identifier,
             _positive(expected_revision, "revision")),
        )
        if row is None:
            raise MetadataConflict()
        return row

    # ── Jobs ──────────────────────────────────────────────────────────────

    async def create_job_once(
        self, identifier: str, payload: dict[str, Any], *, family_id: str = "",
        cohort_id: str = "",
    ) -> tuple[dict[str, Any], bool]:
        """Create a job by id, or return the existing one if it is the same request.

        An id reused with a different kind, candidate or request hash is a
        conflict, never a reset of the existing job.
        """
        await self._bound()
        data = _payload(payload)
        row, created = await self._created_once("jobs", {
            "id": identifier, "family_id": family_id, "cohort_id": cohort_id,
            "version": data.get("version", 1), "state": data.get("state"),
            "payload": Jsonb(data),
        })
        if not created:
            stored = row["payload"]
            if any(stored.get(key) != data.get(key)
                   for key in ("kind", "candidate_id", "request_hash")):
                raise MetadataConflict()
        return row, created

    async def claim_job(self, identifier: str, *, expected_version: int) -> int | None:
        """Start queued work under a new lease; returns its generation, or None if lost."""
        await self._bound()
        current = await self._one(
            "SELECT payload FROM nova_meta.query_autopilot_jobs WHERE id=%s AND state='QUEUED' "
            "AND version=%s FOR UPDATE", (identifier, _positive(expected_version)),
        )
        if current is None:
            return None
        version = expected_version + 1
        data = {**current["payload"], "state": "RUNNING", "version": version}
        row = await self._one(
            "UPDATE nova_meta.query_autopilot_jobs SET state='RUNNING',version=%s,payload=%s,"
            "lease_generation=lease_generation+1 WHERE id=%s AND state='QUEUED' AND version=%s "
            "RETURNING lease_generation",
            (version, Jsonb(_payload(data)), identifier, expected_version),
        )
        return int(row["lease_generation"]) if row else None

    async def retake_job(self, identifier: str, *, older_than_seconds: float) -> int | None:
        """Retake an interrupted run for reconciliation; the old holder is fenced out."""
        await self._bound()
        row = await self._one(
            "UPDATE nova_meta.query_autopilot_jobs SET lease_generation=lease_generation+1 "
            "WHERE id=%s AND state='RUNNING' AND coalesce(heartbeat_at,updated_at) < "
            "clock_timestamp() - make_interval(secs => %s) RETURNING lease_generation",
            (identifier, float(older_than_seconds)),
        )
        return int(row["lease_generation"]) if row else None

    async def heartbeat_job(self, identifier: str, *, generation: int) -> bool:
        await self._bound()
        row = await self._one(
            "UPDATE nova_meta.query_autopilot_jobs SET heartbeat_at=clock_timestamp() "
            "WHERE id=%s AND state='RUNNING' AND lease_generation=%s RETURNING id",
            (identifier, _positive(generation, "lease generation")),
        )
        return row is not None

    async def finish_job(
        self, identifier: str, payload: dict[str, Any], *, generation: int,
    ) -> dict[str, Any] | None:
        """Record the result (``COMPLETED``, ``BLOCKED`` or re-``QUEUED``) by the lease holder.

        Returns None when the lease was lost; the caller must not treat its
        outcome as recorded.
        """
        await self._bound()
        data = _payload(payload)
        if data.get("state") not in (*TERMINAL_JOB, "QUEUED"):
            raise ValueError("A job finishes completed, blocked or queued")
        return await self._one(
            "UPDATE nova_meta.query_autopilot_jobs SET state=%s,version=version+1,payload=%s "
            "WHERE id=%s AND state='RUNNING' AND lease_generation=%s "
            "AND version+1=%s RETURNING *",
            (data["state"], Jsonb(data), identifier,
             _positive(generation, "lease generation"), data.get("version")),
        )

    async def complete_decision(
        self, identifier: str, payload: dict[str, Any], *, expected_version: int,
    ) -> dict[str, Any]:
        """Complete an in-request approve/reject job (web), from the version read."""
        await self._bound()
        data = _payload(payload)
        row = await self._one(
            "UPDATE nova_meta.query_autopilot_jobs SET state='COMPLETED',version=version+1,"
            "payload=%s WHERE id=%s AND state='RUNNING' AND version=%s RETURNING *",
            (Jsonb(data), identifier, _positive(expected_version)),
        )
        if row is None:
            raise MetadataConflict()
        return row

    async def save_checkpoint(self, identifier: str, payload: dict[str, Any]) -> dict[str, Any]:
        """A worker cursor kept in the jobs table, as in the source."""
        await self._bound()
        data = _payload(payload)
        row = await self._one(
            "INSERT INTO nova_meta.query_autopilot_jobs (id,family_id,cohort_id,version,state,"
            "payload) VALUES (%s,'','',1,'CHECKPOINT',%s) ON CONFLICT (id) DO UPDATE SET "
            "payload=EXCLUDED.payload WHERE query_autopilot_jobs.state='CHECKPOINT' RETURNING *",
            (identifier, Jsonb(data)),
        )
        if row is None:
            raise MetadataConflict()
        return row

    async def purge_finished_jobs(self, before: datetime) -> int:
        """Delete completed aggregate/cleanup jobs finished before ``before``."""
        await self._bound()
        cursor = await self.connection.execute(
            "DELETE FROM nova_meta.query_autopilot_jobs WHERE state='COMPLETED' "
            "AND payload->>'kind' IN ('aggregate','cleanup') AND updated_at<%s", (before,),
        )
        return cursor.rowcount

    # ── Fenced results ────────────────────────────────────────────────────

    async def save_experiment(
        self, identifier: str, payload: dict[str, Any], *, job_id: str, generation: int,
        family_id: str, cohort_id: str, state: str,
    ) -> dict[str, Any]:
        """Write the experiment record while ``job_id`` holds ``generation``."""
        await self._bound()
        row = await self._one(
            "INSERT INTO nova_meta.query_autopilot_experiments (id,family_id,cohort_id,version,"
            "state,payload,job_id,job_generation) VALUES (%s,%s,%s,1,%s,%s,%s,%s) "
            "ON CONFLICT (id) DO UPDATE SET state=EXCLUDED.state,payload=EXCLUDED.payload,"
            "job_generation=EXCLUDED.job_generation RETURNING *",
            (identifier, family_id, cohort_id, state, Jsonb(_payload(payload)), job_id,
             _positive(generation, "lease generation")),
        )
        assert row is not None
        return row

    async def record_action_intent(
        self, identifier: str, payload: dict[str, Any], *, job_id: str, generation: int,
        family_id: str = "", cohort_id: str = "",
    ) -> tuple[dict[str, Any], bool]:
        """Persist the intent before the engine change.

        ``created`` is False when the intent already exists: the same intent is
        returned for reconciliation (never a reason to re-submit); a different
        one is a conflict.
        """
        await self._bound()
        data = _payload(payload)
        row, created = await self._created_once("actions", {
            "id": identifier, "family_id": family_id, "cohort_id": cohort_id, "version": 1,
            "state": data.get("state"), "payload": Jsonb(data), "job_id": job_id,
            "job_generation": _positive(generation, "lease generation"),
        })
        if not created and (row["job_id"] != job_id or (
                row["state"] == "APPLYING" and row["payload"] != data)):
            raise MetadataConflict()
        return row, created

    async def acknowledge_action(
        self, identifier: str, payload: dict[str, Any], *, job_id: str, generation: int,
    ) -> dict[str, Any]:
        """Record the verified engine change once; an identical retry is accepted."""
        await self._bound()
        data = _payload(payload)
        row = await self._one(
            "UPDATE nova_meta.query_autopilot_actions SET state=%s,payload=%s,job_generation=%s "
            "WHERE id=%s AND job_id=%s AND state='APPLYING' RETURNING *",
            (data.get("state"), Jsonb(data), _positive(generation, "lease generation"),
             identifier, job_id),
        )
        if row is not None:
            return row
        existing = await self._one(
            "SELECT * FROM nova_meta.query_autopilot_actions WHERE id=%s", (identifier,),
        )
        if existing is None or existing["job_id"] != job_id or existing["payload"] != data:
            raise MetadataConflict()
        return existing

    async def record_outcome(
        self, identifier: str, payload: dict[str, Any], *, job_id: str, generation: int,
        family_id: str, cohort_id: str, state: str,
    ) -> tuple[dict[str, Any], bool]:
        """Record a measured outcome once; an identical retry returns it, a different one fails."""
        await self._bound()
        data = _payload(payload)
        row, created = await self._created_once("outcomes", {
            "id": identifier, "family_id": family_id, "cohort_id": cohort_id, "version": 1,
            "state": state, "payload": Jsonb(data), "job_id": job_id,
            "job_generation": _positive(generation, "lease generation"),
        })
        if not created and (row["payload"] != data or row["state"] != state):
            raise MetadataConflict()
        return row, created

    async def resolve_rollback(
        self, identifier: str, rollback: str, *, job_id: str, generation: int,
    ) -> dict[str, Any]:
        """Resolve a pending rollback on an outcome, once."""
        await self._bound()
        row = await self._one(
            "UPDATE nova_meta.query_autopilot_outcomes SET payload=jsonb_set(payload,"
            "'{rollback}',%s),job_generation=%s WHERE id=%s AND job_id=%s "
            "AND payload->>'rollback'='pending' RETURNING *",
            (Jsonb(rollback), _positive(generation, "lease generation"), identifier, job_id),
        )
        if row is None:
            raise MetadataConflict()
        return row

    # ── Evidence, facts and summaries ─────────────────────────────────────

    async def put_evidence(
        self, identifier: str, payload: dict[str, Any], *, family_id: str, cohort_id: str,
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Create or complete evidence; collected evidence can afterwards only expire."""
        await self._bound()
        row = await self._one(
            "INSERT INTO nova_meta.query_autopilot_evidence (id,family_id,cohort_id,version,"
            "state,payload,expires_at) VALUES (%s,%s,%s,1,'',%s,%s) ON CONFLICT (id) DO UPDATE "
            "SET payload=EXCLUDED.payload,expires_at=EXCLUDED.expires_at RETURNING *",
            (identifier, family_id, cohort_id, Jsonb(_payload(payload)), expires_at),
        )
        assert row is not None
        return row

    async def delete_expired_evidence(self, identifier: str) -> bool:
        await self._bound()
        cursor = await self.connection.execute(
            "DELETE FROM nova_meta.query_autopilot_evidence WHERE id=%s "
            "AND payload->>'availability'='expired'", (identifier,),
        )
        return cursor.rowcount == 1

    async def record_observations(self, observations: list[dict[str, Any]]) -> int:
        """Insert a bounded observation batch; returns how many were new.

        Each item is ``{"id", "family_id", "cohort_id", "state", "payload",
        "observed_at", "expires_at"}``. A retried identical observation is
        accepted; a different one under an existing id is refused.
        """
        await self._bound()
        if len(observations) > MAX_OBSERVATIONS:
            raise ValueError("Observation batch exceeds bound")
        created = 0
        for item in observations:
            data = _payload(item["payload"])
            row, new = await self._created_once("observations", {
                "id": item["id"], "family_id": item["family_id"],
                "cohort_id": item["cohort_id"], "version": 1, "state": item["state"],
                "payload": Jsonb(data), "created_at": item["observed_at"],
                "expires_at": item.get("expires_at"),
            })
            if not new and (row["payload"] != data or row["family_id"] != item["family_id"]
                            or row["cohort_id"] != item["cohort_id"]):
                raise MetadataConflict()
            created += new
        return created

    async def record_incident_once(
        self, identifier: str, payload: dict[str, Any], *, family_id: str, cohort_id: str,
    ) -> tuple[dict[str, Any], bool]:
        """The first detection of an incident is kept, as the source's insert-only write."""
        await self._bound()
        data = _payload(payload)
        return await self._created_once("incidents", {
            "id": identifier, "family_id": family_id, "cohort_id": cohort_id, "version": 1,
            "state": data.get("state", "OPEN"), "payload": Jsonb(data),
        })

    async def put_summary(
        self, kind: str, identifier: str, payload: dict[str, Any], *, family_id: str,
        cohort_id: str, state: str = "", created_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Create or refresh a family, rollup window or baseline (worker-derived)."""
        await self._bound()
        table = _table(kind, SUMMARIES)
        columns = "id,family_id,cohort_id,version,state,payload"
        values: tuple[Any, ...] = (identifier, family_id, cohort_id, 1, state,
                                   Jsonb(_payload(payload)))
        if created_at is not None:
            if kind != "baselines":
                raise ValueError("Only a baseline names its window start")
            columns += ",created_at"
            values += (created_at,)
        row = await self._one(
            f"INSERT INTO nova_meta.{table} ({columns}) VALUES ("
            + ",".join(["%s"] * len(values)) + ") ON CONFLICT (id) DO UPDATE SET "
            "state=EXCLUDED.state,payload=EXCLUDED.payload RETURNING *",
            values,
        )
        assert row is not None
        return row

    async def cleanup_before(self, kind: str, before: datetime) -> int:
        """Age out raw measurements and summaries; durable decisions are never swept."""
        await self._bound()
        table = _table(kind, RETAINED)
        cursor = await self.connection.execute(
            f"DELETE FROM nova_meta.{table} WHERE created_at<%s", (before,),
        )
        return cursor.rowcount
