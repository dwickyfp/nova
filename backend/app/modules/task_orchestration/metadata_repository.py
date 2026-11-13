"""Task graph metadata in the caller's PostgreSQL transaction.

The prepared replacement for the ``NOVA_SYSTEM`` paths of
:class:`TaskOrchestrationRepository`, switched in at the all-domain cutover.
It closes the Wave B gaps in the new store:

* a definition change must name the version it read (CAS) and returns the row
  that transaction wrote;
* the failure counter is one atomic ``UPDATE … RETURNING``;
* graph and node runs are created once by id, and a claim advances a lease
  generation, so heartbeats and transitions from a worker holding an older
  claim are refused instead of overwriting a newer worker's progress;
* a scoped task name compares null scopes deliberately (``IS NOT DISTINCT
  FROM``), never as "matches anything".

Engine observations (native task traces, timezones, load evidence) stay with
their engine adapters; this repository holds only Nova control rows.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied
from app.modules.access_control.security_context import SecurityContext

_DEFINITION = (
    "name", "database_name", "schema_name", "definition", "schedule_kind", "schedule_expr",
    "timezone", "when_expr", "overlap_policy", "owner_role", "handler_config", "when_ast",
)
TERMINAL_GRAPH = ("success", "failed", "cancelled")
TERMINAL_NODE = ("success", "failed", "skipped", "suspended")


def node_run_id(graph_run_id: str, task_id: str, attempt: int) -> str:
    """The same deterministic node-attempt id the source repository uses."""
    return str(uuid5(NAMESPACE_URL, f"nova:task_run:{graph_run_id}:{task_id}:{attempt}"))


def _states(states: list[str] | tuple[str, ...]) -> list[str]:
    if not states or not all(isinstance(state, str) and state for state in states):
        raise ValueError("At least one source state is required")
    return list(states)


def _generation(value: int) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("A positive lease generation is required")
    return value


class TaskMetadataRepository:
    def __init__(
        self, connection: psycopg.AsyncConnection[dict[str, Any]], actor: SecurityContext,
    ) -> None:
        self.connection = connection
        self.actor = actor

    async def _bound(self) -> None:
        if self.connection.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError("Task metadata requires an active metadata transaction")
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

    # ── Definitions ───────────────────────────────────────────────────────

    async def create_task(self, data: dict[str, Any]) -> dict[str, Any]:
        await self._bound()
        values = {field: data.get(field) for field in _DEFINITION}
        values["schedule_kind"] = values["schedule_kind"] or "manual"
        values["overlap_policy"] = values["overlap_policy"] or "skip"
        if values["handler_config"] is not None:
            values["handler_config"] = Jsonb(values["handler_config"])
        row = await self._one(
            "INSERT INTO nova_meta.config_tasks (id,created_by,handler,lifecycle_state,"
            + ",".join(_DEFINITION) + ") VALUES (%s,%s,%s,%s,"
            + ",".join(["%s"] * len(_DEFINITION)) + ") RETURNING *",
            # ``created_by`` is the delegated execution owner. The owning service
            # authorizes it; it defaults to the actor that defines the task.
            (data.get("id") or str(uuid4()), data.get("created_by") or self.actor.principal,
             data.get("handler") or "sql",
             data.get("lifecycle_state") or "STARTED", *values.values()),
        )
        assert row is not None
        return row

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one("SELECT * FROM nova_meta.config_tasks WHERE id=%s", (task_id,))

    async def find_task(
        self, name: str, database: str | None, schema: str | None,
    ) -> list[dict[str, Any]]:
        """Every task with this scoped name; more than one is an unreconciled import."""
        await self._bound()
        cursor = await self.connection.execute(
            "SELECT * FROM nova_meta.config_tasks WHERE name=%s "
            "AND database_name IS NOT DISTINCT FROM %s AND schema_name IS NOT DISTINCT FROM %s "
            "ORDER BY id", (name, database, schema),
        )
        return list(await cursor.fetchall())

    async def update_task(
        self, task_id: str, changes: dict[str, Any], *, expected_version: int,
    ) -> dict[str, Any]:
        """Apply a definition change only if the task is still at ``expected_version``."""
        await self._bound()
        unknown = set(changes) - set(_DEFINITION)
        if unknown or not changes:
            raise ValueError(f"Unsupported task changes: {sorted(unknown)}")
        _generation(expected_version)
        values = [
            Jsonb(value) if field == "handler_config" and value is not None else value
            for field, value in changes.items()
        ]
        assignments = ",".join(f"{field}=%s" for field in changes)
        row = await self._one(
            f"UPDATE nova_meta.config_tasks SET {assignments},version=version+1 "
            "WHERE id=%s AND version=%s RETURNING *",
            (*values, task_id, expected_version),
        )
        if row is None:
            raise MetadataConflict()
        return row

    async def set_lifecycle(self, task_id: str, state: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "UPDATE nova_meta.config_tasks SET lifecycle_state=%s WHERE id=%s RETURNING *",
            (state, task_id),
        )

    async def delete_task(self, task_id: str) -> bool:
        await self._bound()
        cursor = await self.connection.execute(
            "DELETE FROM nova_meta.config_tasks WHERE id=%s", (task_id,),
        )
        return cursor.rowcount == 1

    async def increment_consecutive_failures(self, task_id: str) -> int:
        await self._bound()
        row = await self._one(
            "UPDATE nova_meta.config_tasks SET consecutive_fail_count="
            "coalesce(consecutive_fail_count,0)+1 WHERE id=%s RETURNING consecutive_fail_count",
            (task_id,),
        )
        return int(row["consecutive_fail_count"]) if row else 0

    async def reset_consecutive_failures(self, task_id: str) -> None:
        await self._bound()
        await self.connection.execute(
            "UPDATE nova_meta.config_tasks SET consecutive_fail_count=0 "
            "WHERE id=%s AND consecutive_fail_count<>0", (task_id,),
        )

    # ── Edges ─────────────────────────────────────────────────────────────

    async def create_edge(self, graph_id: str, data: dict[str, Any]) -> dict[str, Any]:
        await self._bound()
        row = await self._one(
            "INSERT INTO nova_meta.config_task_edges (id,graph_id,parent_task,child_task,"
            "edge_kind) VALUES (%s,%s,%s,%s,%s) RETURNING *",
            (str(uuid4()), graph_id, data["parent_task"], data["child_task"],
             data.get("edge_kind") or "after"),
        )
        assert row is not None
        return row

    async def list_edges(self, graph_id: str) -> list[dict[str, Any]]:
        await self._bound()
        cursor = await self.connection.execute(
            "SELECT * FROM nova_meta.config_task_edges WHERE graph_id=%s "
            "ORDER BY created_at NULLS FIRST,id", (graph_id,),
        )
        return list(await cursor.fetchall())

    async def delete_edge(self, edge_id: str) -> bool:
        await self._bound()
        cursor = await self.connection.execute(
            "DELETE FROM nova_meta.config_task_edges WHERE id=%s", (edge_id,),
        )
        return cursor.rowcount == 1

    # ── Graph runs ────────────────────────────────────────────────────────

    async def create_graph_run_once(self, data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Create a pending run by id, or return the existing one untouched."""
        await self._bound()
        run_id = data.get("id") or str(uuid4())
        wal = data.get("wal_marks")
        row = await self._one(
            "INSERT INTO nova_meta.config_task_graph_runs (id,graph_id,trigger_type,state,"
            "overlap_policy,wal_marks,execution_user,execution_role,execution_session_id,job_id) "
            "VALUES (%s,%s,%s,'pending',%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING "
            "RETURNING *",
            (run_id, data["graph_id"], data.get("trigger_type") or "manual",
             data.get("overlap_policy") or "skip",
             json.dumps(wal, sort_keys=True) if wal is not None else None,
             data.get("execution_user"), data.get("execution_role"),
             data.get("execution_session_id"), data.get("job_id")),
        )
        if row is not None:
            return row, True
        existing = await self.get_graph_run(run_id)
        if existing is None or existing["graph_id"] != data["graph_id"]:
            raise MetadataConflict()
        return existing, False

    async def get_graph_run(self, run_id: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_task_graph_runs WHERE id=%s", (run_id,),
        )

    async def existing_graph_run_ids(self, run_ids: list[str]) -> set[str]:
        await self._bound()
        found: set[str] = set()
        for start in range(0, len(run_ids), 1000):
            rows = await self._all(
                "SELECT id FROM nova_meta.config_task_graph_runs WHERE id=ANY(%s)",
                (run_ids[start:start + 1000],),
            )
            found.update(row["id"] for row in rows)
        return found

    async def claim_graph_run(
        self, run_id: str, from_states: list[str], to_state: str,
    ) -> int | None:
        """Move the run and take a new lease; returns its generation, or None if lost."""
        await self._bound()
        row = await self._one(
            "UPDATE nova_meta.config_task_graph_runs SET state=%s,heartbeat_at=clock_timestamp(),"
            "lease_generation=lease_generation+1 WHERE id=%s AND state=ANY(%s) "
            "AND state<>ALL(%s) RETURNING lease_generation",
            (to_state, run_id, _states(from_states), list(TERMINAL_GRAPH)),
        )
        return int(row["lease_generation"]) if row else None

    async def transition_graph_run(
        self, run_id: str, from_states: list[str], to_state: str, *, generation: int,
    ) -> bool:
        """A state change by the current lease holder only."""
        await self._bound()
        row = await self._one(
            "UPDATE nova_meta.config_task_graph_runs SET state=%s WHERE id=%s "
            "AND state=ANY(%s) AND lease_generation=%s RETURNING id",
            (to_state, run_id, _states(from_states), _generation(generation)),
        )
        return row is not None

    async def heartbeat_graph_run(self, run_id: str, *, generation: int) -> bool:
        await self._bound()
        row = await self._one(
            "UPDATE nova_meta.config_task_graph_runs SET heartbeat_at=clock_timestamp() "
            "WHERE id=%s AND lease_generation=%s AND state<>ALL(%s) RETURNING id",
            (run_id, _generation(generation), list(TERMINAL_GRAPH)),
        )
        return row is not None

    async def latest_graph_run(self, graph_id: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_task_graph_runs WHERE graph_id=%s "
            "ORDER BY started_at DESC NULLS LAST,id DESC LIMIT 1", (graph_id,),
        )

    # ── Node runs ─────────────────────────────────────────────────────────

    async def create_task_run_once(
        self, graph_run_id: str, task_id: str, attempt: int = 1, *, delegated: bool = True,
    ) -> tuple[dict[str, Any], bool]:
        """Create one node attempt by its deterministic id, never resetting a raced row."""
        await self._bound()
        if type(attempt) is not int or attempt < 1:
            raise ValueError("A positive attempt is required")
        run_id = node_run_id(graph_run_id, task_id, attempt)
        row = await self._one(
            "INSERT INTO nova_meta.config_task_runs (id,graph_run_id,task_id,attempt,state,"
            "delegated) VALUES (%s,%s,%s,%s,'pending',%s) ON CONFLICT (id) DO NOTHING "
            "RETURNING *", (run_id, graph_run_id, task_id, attempt, delegated),
        )
        if row is not None:
            return row, True
        existing = await self._one(
            "SELECT * FROM nova_meta.config_task_runs WHERE id=%s", (run_id,),
        )
        assert existing is not None
        return existing, False

    async def claim_task_run(self, run_id: str, from_states: list[str]) -> int | None:
        """Start (or retake an abandoned) node under a new lease generation."""
        await self._bound()
        row = await self._one(
            "UPDATE nova_meta.config_task_runs SET state='running',"
            "heartbeat_at=clock_timestamp(),lease_generation=lease_generation+1 "
            "WHERE id=%s AND state=ANY(%s) AND state<>ALL(%s) RETURNING lease_generation",
            (run_id, _states(from_states), list(TERMINAL_NODE)),
        )
        return int(row["lease_generation"]) if row else None

    async def finish_task_run(
        self, run_id: str, to_state: str, *, generation: int, query_id: str | None = None,
        error_message: str | None = None, output: str | None = None,
    ) -> bool:
        """Record the outcome, only for the worker holding the current lease."""
        await self._bound()
        row = await self._one(
            "UPDATE nova_meta.config_task_runs SET state=%s,"
            "starrocks_query_id=coalesce(%s,starrocks_query_id),"
            "error_message=coalesce(%s,error_message),output=coalesce(%s,output) "
            "WHERE id=%s AND state='running' AND lease_generation=%s RETURNING id",
            (to_state, query_id, error_message, output, run_id, _generation(generation)),
        )
        return row is not None

    async def heartbeat_task_run(self, run_id: str, *, generation: int) -> bool:
        await self._bound()
        row = await self._one(
            "UPDATE nova_meta.config_task_runs SET heartbeat_at=clock_timestamp() "
            "WHERE id=%s AND state='running' AND lease_generation=%s RETURNING id",
            (run_id, _generation(generation)),
        )
        return row is not None

    async def abandon_stale_task_runs(self, *, older_than_seconds: int) -> list[str]:
        """Mark running nodes with a lapsed heartbeat abandoned; their lease ends."""
        await self._bound()
        cursor = await self.connection.execute(
            "UPDATE nova_meta.config_task_runs SET state='abandoned' WHERE state='running' "
            "AND heartbeat_at < clock_timestamp() - make_interval(secs => %s) RETURNING id",
            (float(older_than_seconds),),
        )
        return [row["id"] for row in await cursor.fetchall()]

    async def list_unsettled_abandoned_task_runs(self, *, limit: int = 200) -> list[dict[str, Any]]:
        """Abandoned nodes whose graph run is still active, oldest first."""
        await self._bound()
        return await self._all(
            "SELECT r.* FROM nova_meta.config_task_runs r JOIN nova_meta.config_task_graph_runs g "
            "ON g.id=r.graph_run_id WHERE r.state='abandoned' AND g.state IN ('pending','running') "
            "ORDER BY r.started_at,r.id LIMIT %s", (max(1, int(limit)),),
        )

    async def settle_abandoned_task_run(
        self, run_id: str, to_state: str, *, generation: int, query_id: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        """Record a verified outcome (or an explanation) on an abandoned node.

        Fenced on the generation read with the row, so a node retaken since is
        never overwritten by a stale reconciliation.
        """
        await self._bound()
        row = await self._one(
            "UPDATE nova_meta.config_task_runs SET state=%s,"
            "starrocks_query_id=coalesce(%s,starrocks_query_id),"
            "error_message=coalesce(%s,error_message) WHERE id=%s "
            "AND state='abandoned' AND lease_generation=%s RETURNING id",
            (to_state, query_id, error_message, run_id, _generation(generation)),
        )
        return row is not None

    async def list_task_runs(self, graph_run_id: str) -> list[dict[str, Any]]:
        await self._bound()
        cursor = await self.connection.execute(
            "SELECT * FROM nova_meta.config_task_runs WHERE graph_run_id=%s "
            "ORDER BY task_id,attempt", (graph_run_id,),
        )
        return list(await cursor.fetchall())

    # ── Role bindings ─────────────────────────────────────────────────────

    async def bind_role_execution_user(self, role: str, user: str) -> dict[str, Any]:
        await self._bound()
        row = await self._one(
            "INSERT INTO nova_meta.config_task_role_bindings (role_name,execution_user,"
            "configured_by) VALUES (%s,%s,%s) ON CONFLICT (role_name) DO UPDATE SET "
            "execution_user=EXCLUDED.execution_user,configured_by=EXCLUDED.configured_by,"
            "metadata_revision=config_task_role_bindings.metadata_revision+1 RETURNING *",
            (role, user, self.actor.principal),
        )
        assert row is not None
        return row

    async def get_role_execution_user(self, role: str) -> str | None:
        await self._bound()
        row = await self._one(
            "SELECT execution_user FROM nova_meta.config_task_role_bindings WHERE role_name=%s",
            (role,),
        )
        return str(row["execution_user"]) if row else None

    # ── Read model and runtime queries used by the ported callers ─────────

    async def _all(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        cursor = await self.connection.execute(query, params)
        return list(await cursor.fetchall())

    async def list_tasks(self, graph_id: str | None = None) -> list[dict[str, Any]]:
        """All tasks, or the members of one graph (root scope plus edge endpoints).

        A graph id is the root's qualified name (``database.schema.name``); a
        legacy two-part id has no schema and a bare id is an unscoped task or a
        task id. Membership never matches across scopes.
        """
        await self._bound()
        if graph_id is None:
            return await self._all("SELECT * FROM nova_meta.config_tasks ORDER BY name,id")
        members = (
            "name IN (SELECT parent_task FROM nova_meta.config_task_edges WHERE graph_id=%s "
            "UNION SELECT child_task FROM nova_meta.config_task_edges WHERE graph_id=%s)"
        )
        parts = graph_id.split(".")
        if len(parts) == 3 and all(parts):
            database, schema = parts[0], parts[1]
            root = graph_id.rsplit(".", 1)[-1]
            return await self._all(
                "SELECT * FROM nova_meta.config_tasks WHERE database_name=%s AND schema_name=%s "
                f"AND (name=%s OR {members}) ORDER BY name,id",
                (database, schema, root, graph_id, graph_id),
            )
        if len(parts) == 2:
            return await self._all(
                "SELECT * FROM nova_meta.config_tasks WHERE database_name=%s "
                f"AND schema_name IS NULL AND (name=%s OR {members}) ORDER BY name,id",
                (parts[0], parts[1], graph_id, graph_id),
            )
        return await self._all(
            "SELECT * FROM nova_meta.config_tasks WHERE id=%s OR (name=%s "
            f"AND database_name IS NULL AND schema_name IS NULL) OR ({members}) ORDER BY name,id",
            (graph_id, graph_id, graph_id, graph_id),
        )

    async def list_all_edges(self) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all("SELECT * FROM nova_meta.config_task_edges ORDER BY graph_id,id")

    async def list_graph_runs(
        self, graph_id: str, *, trigger_type: str | None = None,
        limit: int = 1000, offset: int = 0,
    ) -> list[dict[str, Any]]:
        """A graph's runs, newest first, optionally of one trigger type."""
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.config_task_graph_runs WHERE graph_id=%s "
            "AND (%s::text IS NULL OR trigger_type=%s) "
            "ORDER BY started_at DESC NULLS LAST,id DESC LIMIT %s OFFSET %s",
            (graph_id, trigger_type, trigger_type, max(1, int(limit)), max(0, int(offset))),
        )

    async def latest_graph_runs(self) -> list[dict[str, Any]]:
        """Each graph's newest run, in one query."""
        await self._bound()
        return await self._all(
            "SELECT DISTINCT ON (graph_id) * FROM nova_meta.config_task_graph_runs "
            "ORDER BY graph_id,started_at DESC NULLS LAST,id DESC",
        )

    async def count_graph_runs(self, graph_id: str) -> int:
        await self._bound()
        row = await self._one(
            "SELECT count(*) AS total FROM nova_meta.config_task_graph_runs WHERE graph_id=%s",
            (graph_id,),
        )
        return int(row["total"]) if row else 0

    async def count_graph_runs_by_graph(self) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT graph_id,count(*) AS total,"
            "count(*) FILTER (WHERE state='success') AS success,"
            "count(*) FILTER (WHERE state='failed') AS failed "
            "FROM nova_meta.config_task_graph_runs GROUP BY graph_id",
        )

    async def list_graph_runs_by_state(
        self, states: list[str], *, limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Runs in ``states``, oldest first — the recovery work set."""
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.config_task_graph_runs WHERE state=ANY(%s) "
            "ORDER BY started_at NULLS FIRST,id LIMIT %s",
            (_states(states), max(1, int(limit))),
        )

    async def list_active_graph_runs(self, graph_id: str) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.config_task_graph_runs WHERE graph_id=%s "
            "AND state IN ('pending','running') ORDER BY started_at NULLS FIRST,id",
            (graph_id,),
        )

    async def graph_run_for_job(self, job_id: Any) -> dict[str, Any] | None:
        """The run a durable ``task.graph`` job delivers."""
        await self._bound()
        return await self._one(
            "SELECT * FROM nova_meta.config_task_graph_runs WHERE job_id=%s", (job_id,),
        )

    async def list_stale_graph_runs(
        self, *, older_than_seconds: int, limit: int = 200,
    ) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.config_task_graph_runs WHERE state='running' "
            "AND coalesce(heartbeat_at,started_at) < clock_timestamp() - make_interval(secs => %s) "
            "ORDER BY started_at,id LIMIT %s",
            (float(older_than_seconds), max(1, int(limit))),
        )

    async def get_task_run(self, run_id: str) -> dict[str, Any] | None:
        await self._bound()
        return await self._one("SELECT * FROM nova_meta.config_task_runs WHERE id=%s", (run_id,))

    async def list_task_runs_for_graph(self, graph_id: str) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT r.* FROM nova_meta.config_task_runs r JOIN nova_meta.config_task_graph_runs g "
            "ON g.id=r.graph_run_id WHERE g.graph_id=%s ORDER BY r.started_at,r.id",
            (graph_id,),
        )

    async def list_running_task_runs(self, *, limit: int = 500) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.config_task_runs WHERE state='running' "
            "ORDER BY started_at,id LIMIT %s", (max(1, int(limit)),),
        )

    async def list_stale_task_runs(
        self, *, older_than_seconds: int, limit: int = 200,
    ) -> list[dict[str, Any]]:
        await self._bound()
        return await self._all(
            "SELECT * FROM nova_meta.config_task_runs WHERE state='running' "
            "AND coalesce(heartbeat_at,started_at) < clock_timestamp() - make_interval(secs => %s) "
            "ORDER BY started_at,id LIMIT %s",
            (float(older_than_seconds), max(1, int(limit))),
        )

    async def skip_pending_task_run(self, run_id: str) -> bool:
        """A node that never started is recorded skipped; no lease is involved."""
        await self._bound()
        row = await self._one(
            "UPDATE nova_meta.config_task_runs SET state='skipped' "
            "WHERE id=%s AND state='pending' RETURNING id", (run_id,),
        )
        return row is not None

    async def abandon_task_run(
        self, run_id: str, *, generation: int, query_id: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        """End one stale lease, only if nobody retook the node since it was read."""
        await self._bound()
        row = await self._one(
            "UPDATE nova_meta.config_task_runs SET state='abandoned',"
            "starrocks_query_id=coalesce(%s,starrocks_query_id),"
            "error_message=coalesce(%s,error_message) WHERE id=%s "
            "AND state='running' AND lease_generation=%s RETURNING id",
            (query_id, error_message, run_id, _generation(generation)),
        )
        return row is not None
