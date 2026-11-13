"""Revisioned preference storage for the PostgreSQL cutover.

Callers own value schemas and secret encryption. PostgreSQL owns row access.
The service supplies its actor-bound transaction so preference, audit and job
writes can commit together. This store never commits or retries the outer transaction.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import psycopg
from psycopg.pq import TransactionStatus

from app.common.crypto import EncryptionError, decrypt
from app.core.metadata_db import MetadataConflict


def _secret_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError("Duplicate credential fields")
    return fields


def _text(value: str, limit: int) -> None:
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise ValueError("Invalid preference value")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValueError("Invalid preference value") from None


@dataclass(frozen=True, slots=True)
class PreferenceChange:
    expected_revision: int
    value: str | None = field(repr=False)
    deleted: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.expected_revision) is not int
            or not 0 <= self.expected_revision < 2**63 - 1
            or type(self.deleted) is not bool
            or (self.deleted and (self.expected_revision == 0 or self.value is not None))
        ):
            raise ValueError("Invalid preference revision or deletion")
        if self.value is not None:
            _text(self.value, 65533)


@dataclass(frozen=True, slots=True)
class PreferenceRecord:
    value: str | None = field(repr=False)
    revision: int
    deleted: bool
    updated_at: datetime | None


class PreferenceRepository:
    def __init__(self, connection: psycopg.AsyncConnection[dict[str, Any]]) -> None:
        self._connection = connection
        self._require_transaction()

    def _require_transaction(self) -> None:
        if self._connection.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError("Preference storage requires an active metadata transaction")

    @staticmethod
    def _validate(owner: str, keys: Sequence[str]) -> None:
        _text(owner, 128)
        if not owner or not 1 <= len(keys) <= 100 or len(set(keys)) != len(keys):
            raise ValueError("Invalid preference selection")
        for key in keys:
            _text(key, 128)
            if not key:
                raise ValueError("Invalid preference selection")

    async def read(
        self,
        owner: str,
        keys: Sequence[str],
    ) -> dict[str, PreferenceRecord]:
        self._require_transaction()
        keys = tuple(keys)
        self._validate(owner, keys)
        cursor = await self._connection.execute(
            "SELECT pref_key, pref_value, revision, is_deleted, updated_at "
            "FROM nova_meta.config_user_preferences "
            "WHERE user_name=%s AND pref_key=ANY(%s)",
            (owner, list(keys)),
        )
        return {
            row["pref_key"]: PreferenceRecord(
                row["pref_value"],
                row["revision"],
                row["is_deleted"],
                row["updated_at"],
            )
            for row in await cursor.fetchall()
        }

    async def change(
        self,
        owner: str,
        changes: Mapping[str, PreferenceChange],
    ) -> dict[str, PreferenceRecord]:
        """Apply a batch atomically; revision zero means the key has never existed.

        Deletion retains a revisioned tombstone. Recreating a key requires its
        current revision, so an old client cannot overwrite a new incarnation.
        """
        self._require_transaction()
        changes = dict(changes)
        self._validate(owner, list(changes))
        if any(not isinstance(change, PreferenceChange) for change in changes.values()):
            raise ValueError("Invalid preference change")
        secret = changes.get("tavily_api_key")
        if owner == "__system__" and secret and not secret.deleted:
            try:
                value = secret.value or ""
                if not value.startswith("enc:"):
                    stored = json.loads(value, object_pairs_hook=_secret_fields)
                    value = stored["api_key"]
                    if set(stored) != {"api_key", "hint"} or stored["hint"] not in (
                        None,
                        "tvly-",
                        "tvly-dev-",
                        "tvly-prod-",
                    ):
                        raise ValueError
                if not isinstance(value, str) or not value.startswith("enc:") or not decrypt(value):
                    raise ValueError
            except (ValueError, TypeError, KeyError, EncryptionError):
                raise ValueError("Search credentials must be encrypted before storage") from None
        records: dict[str, PreferenceRecord] = {}
        async with self._connection.transaction():
            for key, change in sorted(changes.items()):
                if change.expected_revision == 0:
                    cursor = await self._connection.execute(
                        "INSERT INTO nova_meta.config_user_preferences "
                        "(user_name, pref_key, pref_value) VALUES (%s, %s, %s) "
                        "ON CONFLICT (user_name, pref_key) DO NOTHING "
                        "RETURNING pref_value, revision, is_deleted, updated_at",
                        (owner, key, change.value),
                    )
                else:
                    cursor = await self._connection.execute(
                        "UPDATE nova_meta.config_user_preferences SET pref_value=%s, "
                        "revision=revision+1, is_deleted=%s "
                        "WHERE user_name=%s AND pref_key=%s AND revision=%s "
                        "RETURNING pref_value, revision, is_deleted, updated_at",
                        (change.value, change.deleted, owner, key, change.expected_revision),
                    )
                row = await cursor.fetchone()
                if row is None:
                    raise MetadataConflict()
                records[key] = PreferenceRecord(
                    row["pref_value"],
                    row["revision"],
                    row["is_deleted"],
                    row["updated_at"],
                )
        return records
