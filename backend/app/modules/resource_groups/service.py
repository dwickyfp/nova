"""Resource group service — warehouse CRUD + classifier configuration.

Design constraints (roadmap #16, ``docs/12-resource-groups.md``):

- **RBAC is StarRocks-native.** Every statement runs on the caller's connection
  through the shared ``QueryService`` pipeline, so the engine's
  ``CREATE RESOURCE GROUP ON SYSTEM`` privilege decides who may manage groups
  and the pipeline's guard/audit apply unchanged.
- **Quota enforcement is the engine's, never simulated.** Nova writes the
  ``WITH (...)`` attributes and reads back ``SHOW USAGE RESOURCE GROUPS``; it
  never tracks running/queued counts itself.
- **Only documented attributes are serialized.** A property the doc does not
  list is refused rather than passed through, so a request cannot smuggle an
  arbitrary engine setting through the resource-group surface.
"""

from __future__ import annotations

import logging
import re

from app.modules.query.service import query_service

from .schemas import (
    RESOURCE_GROUP_ATTRIBUTES,
    ClassifierSpec,
    ResourceGroupCreate,
)

log = logging.getLogger(__name__)

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ResourceGroupError(ValueError):
    """A resource-group operation the service refused before or after the engine."""


def _safe_ident(value: str, what: str) -> str:
    if not _NAME.match(value or ""):
        raise ResourceGroupError(f"Invalid {what}: {value!r}")
    return value


def _quote(value: str) -> str:
    return f"`{value.replace('`', '``')}`"


def _scalar(value: object) -> str:
    """Serialize one attribute value for the engine's ``WITH (...)`` list.

    Numbers and booleans are rendered bare; strings are validated as a single
    token and rendered bare too. Anything that could close the paren or start a
    new statement is refused, so an attribute value cannot escape the list.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    # A value with whitespace/parens/quotes/semicolons is not a resource-group
    # attribute token; ``warehouses`` is the only multi-valued one and the doc
    # spells it as a comma list.
    if not re.fullmatch(r"[A-Za-z0-9_.,\-]*", text):
        raise ResourceGroupError(f"Invalid resource group attribute value: {value!r}")
    return text


def _serialize_properties(properties: dict) -> str:
    if not properties:
        raise ResourceGroupError("At least one resource group attribute is required")
    parts: list[str] = []
    for key, value in properties.items():
        if key not in RESOURCE_GROUP_ATTRIBUTES:
            raise ResourceGroupError(
                f"Unsupported resource group attribute '{key}'. "
                f"Allowed: {', '.join(RESOURCE_GROUP_ATTRIBUTES)}"
            )
        # ``_scalar`` validates; the key is from the fixed allow-list.
        parts.append(f'"{key}" = "{_scalar(value)}"')
    return ", ".join(parts)


def _serialize_classifier(spec: ClassifierSpec) -> str:
    """``('user'='alice', 'query_type'='select')`` — engine classifier syntax."""
    pairs: list[tuple[str, str]] = []
    for key in ("user", "role", "query_type", "source_ip"):
        value = getattr(spec, key)
        if value is None:
            continue
        token = str(value)
        if not re.fullmatch(r"[A-Za-z0-9_.*%:\-]+", token):
            raise ResourceGroupError(f"Invalid classifier {key}: {value!r}")
        pairs.append((key, token))
    body = ", ".join(f"'{k}' = '{v}'" for k, v in pairs)
    return f"({body})"


class ResourceGroupService:
    """Resource group CRUD + classifier configuration through the pipeline."""

    async def _run(
        self,
        sql: str,
        *,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
        allow_destructive: bool = False,
    ) -> None:
        """Execute one resource-group statement as the caller."""
        result = await query_service.execute(
            sql=sql,
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
            confirm_destructive=allow_destructive,
        )
        if result.error:
            raise ResourceGroupError(result.error)

    async def _query(
        self,
        sql: str,
        *,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ):
        result = await query_service.execute(
            sql=sql,
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )
        if result.error:
            raise ResourceGroupError(result.error)
        return result

    # ── CRUD ────────────────────────────────────────────────────

    async def list_resource_groups(
        self,
        *,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> list[dict]:
        result = await self._query(
            "SHOW RESOURCE GROUPS",
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )
        return _rows_to_groups(result)

    async def create_resource_group(
        self,
        body: ResourceGroupCreate,
        *,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> dict:
        name = _safe_ident(body.name, "resource group name")
        attrs = _serialize_properties(body.properties)
        statement = f"CREATE RESOURCE GROUP {_quote(name)} WITH ({attrs})"
        await self._run(
            statement,
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )
        created: dict = {"name": name, "properties": dict(body.properties), "classifiers": []}
        for spec in body.classifiers:
            await self.add_classifier(
                name,
                spec,
                username=username,
                encrypted_password=encrypted_password,
                session_id=session_id,
                role=role,
            )
            created["classifiers"].append(spec.model_dump())
        return created

    async def alter_resource_group(
        self,
        name: str,
        *,
        properties: dict,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> dict:
        safe_name = _safe_ident(name, "resource group name")
        attrs = _serialize_properties(properties)
        await self._run(
            f"ALTER RESOURCE GROUP {_quote(safe_name)} SET ({attrs})",
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )
        return {"name": safe_name, "properties": dict(properties)}

    async def drop_resource_group(
        self,
        name: str,
        *,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> None:
        safe_name = _safe_ident(name, "resource group name")
        await self._run(
            f"DROP RESOURCE GROUP {_quote(safe_name)}",
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
            allow_destructive=True,
        )

    # ── Classifiers ─────────────────────────────────────────────

    async def add_classifier(
        self,
        name: str,
        spec: ClassifierSpec,
        *,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> None:
        safe_name = _safe_ident(name, "resource group name")
        classifier = _serialize_classifier(spec)
        await self._run(
            f"ALTER RESOURCE GROUP {_quote(safe_name)} ADD {classifier}",
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )

    async def drop_classifier(
        self,
        name: str,
        spec: ClassifierSpec,
        *,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> None:
        safe_name = _safe_ident(name, "resource group name")
        classifier = _serialize_classifier(spec)
        await self._run(
            f"ALTER RESOURCE GROUP {_quote(safe_name)} DROP {classifier}",
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )

    # ── Usage ───────────────────────────────────────────────────

    async def usage(
        self,
        *,
        username: str,
        encrypted_password: str,
        session_id: str | None,
        role: str | None = None,
    ) -> list[dict]:
        """``SHOW USAGE RESOURCE GROUPS`` — engine-reported running/queued counts.

        Nova reports what the engine says; it never computes or stores the
        counts, which is the whole point of *quota enforcement is the engine's*.
        """
        result = await self._query(
            "SHOW USAGE RESOURCE GROUPS",
            username=username,
            encrypted_password=encrypted_password,
            session_id=session_id,
            role=role,
        )
        usage: list[dict] = []
        for row in result.rows or []:
            raw = {
                str(column): value
                for column, value in zip(result.columns, row, strict=False)
            }
            usage.append(
                {
                    "name": str(row[0]) if row else "",
                    "running": _as_int(raw.get("running") or raw.get("Running")),
                    "queued": _as_int(raw.get("queued") or raw.get("Queued")),
                    "raw": raw,
                }
            )
        return usage


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _rows_to_groups(result) -> list[dict]:
    """Normalize ``SHOW RESOURCE GROUPS`` rows into response-shaped dicts.

    The engine's column labels differ across builds; the name is the first
    column and every other column becomes a string property. Classifiers are
    not returned by ``SHOW RESOURCE GROUPS`` on 4.1.x, so the list endpoint
    reports them empty rather than inventing a second query shape.
    """
    groups: list[dict] = []
    columns = [str(c) for c in result.columns]
    for row in result.rows or []:
        values = list(row)
        name = str(values[0]) if values else ""
        properties = {
            str(columns[i]): "" if v is None else str(v)
            for i, v in enumerate(values)
            if i > 0 and i < len(columns)
        }
        groups.append({"name": name, "properties": properties, "classifiers": []})
    return groups


resource_group_service = ResourceGroupService()
