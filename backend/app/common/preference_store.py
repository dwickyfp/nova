"""Service-level reads and writes of preferences in PostgreSQL.

Each call runs in the caller's actor-bound metadata transaction (joining one
already open for the same actor), so a preference change, its generated
success audit and any other domain write commit together. A write reads the
current revision and changes the row with it in that same transaction; a
concurrent writer therefore ends in one success and one ``MetadataConflict``
(409), never a silent overwrite.

Row access is decided by PostgreSQL RLS for the process's workload login and
the actor (see PG_PREFERENCES.md); this module adds no authorization of its own.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.common.preferences import PreferenceChange, PreferenceRecord, PreferenceRepository
from app.core.metadata_scope import metadata_transaction, service_actor
from app.modules.access_control.security_context import SecurityContext

#: Owner of deployment-wide settings rows (setup marker, default model, search key).
SYSTEM_OWNER = "__system__"


def settings_reader() -> SecurityContext:
    """The explicit context for reading deployment settings without a caller.

    Used where no authenticated caller exists (sign-in before a session, and
    provider selection outside a request). RLS lets any valid actor read the
    default model and lets the web login read the setup marker; this context
    grants nothing else and can write nothing.
    """
    return service_actor("nova_settings_reader", "PUBLIC")


async def read_preferences(
    actor: SecurityContext, owner: str, keys: Sequence[str],
) -> dict[str, PreferenceRecord]:
    """Live and tombstoned records for ``keys`` that RLS lets ``actor`` see."""
    async with metadata_transaction(actor, read_only=True) as scope:
        return await PreferenceRepository(scope.connection).read(owner, keys)


async def read_values(
    actor: SecurityContext, owner: str, keys: Sequence[str],
) -> dict[str, str | None]:
    """The current value of each visible, non-deleted key."""
    records = await read_preferences(actor, owner, keys)
    return {key: record.value for key, record in records.items() if not record.deleted}


async def read_value(actor: SecurityContext, owner: str, key: str) -> str | None:
    return (await read_values(actor, owner, [key])).get(key)


async def write_values(
    actor: SecurityContext, owner: str, values: Mapping[str, str | None],
) -> dict[str, PreferenceRecord]:
    """Set (``str``) or delete (``None``) keys at their current revisions.

    An unchanged value or deleting an absent key is a no-op, so a repeated
    request does not advance the revision or write another audit event.
    Returns the records that changed.
    """
    if not values:
        return {}
    async with metadata_transaction(actor) as scope:
        repository = PreferenceRepository(scope.connection)
        current = await repository.read(owner, list(values))
        changes: dict[str, PreferenceChange] = {}
        for key, value in values.items():
            record = current.get(key)
            if value is None:
                if record is not None and not record.deleted:
                    changes[key] = PreferenceChange(record.revision, None, deleted=True)
            elif record is None:
                changes[key] = PreferenceChange(0, value)
            elif record.deleted or record.value != value:
                changes[key] = PreferenceChange(record.revision, value)
        if not changes:
            return {}
        return await repository.change(owner, changes)


__all__ = [
    "SYSTEM_OWNER",
    "read_preferences",
    "read_value",
    "read_values",
    "settings_reader",
    "write_values",
]
