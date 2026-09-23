"""Schema-scoped Semantic View lifecycle over Nova's existing Ossie IR/compiler."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.common.audit import write_audit_log
from app.common.responses import SanitizingJSONResponse
from app.core.database import db
from app.core.deps import get_current_user
from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.ossie import OssieParseError, parse_ossie
from app.modules.agents.semantic.planning import SemanticPlan
from app.modules.agents.semantic.runtime import validate_semantic_model_ir
from app.modules.agents.semantic.verification import verify_sql_compatibility
from app.modules.intelligence.access import can_manage
from app.modules.intelligence.entities import entity_registry
from app.modules.intelligence.semantic_regression import (
    MAX_RESULT_ROWS,
    MAX_VERIFIED_QUERIES,
    compare_semantic_versions,
)
from app.modules.query.service import query_service

router = APIRouter()
CurrentUser = Annotated[dict, Depends(get_current_user)]
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


class SemanticViewCreate(BaseModel):
    name: str = Field(pattern=_IDENT.pattern)
    catalog: str = Field(default="default_catalog", pattern=_IDENT.pattern)
    database: str = Field(pattern=_IDENT.pattern)
    schema_name: str = Field(default="", max_length=128)
    definition: str = Field(min_length=1, max_length=1_000_000)


class SemanticViewVersionCreate(BaseModel):
    definition: str = Field(min_length=1, max_length=1_000_000)


class SemanticViewQuery(BaseModel):
    metrics: list[str] = Field(default_factory=list, max_length=32)
    dimensions: list[str] = Field(default_factory=list, max_length=32)
    filters: dict[str, str | int | float | bool] = Field(default_factory=dict, max_length=32)
    named_filters: list[str] = Field(default_factory=list, max_length=16)
    limit: int = Field(default=100, ge=1, le=1000)
    version: int | None = Field(default=None, ge=1)


class SemanticViewPublish(BaseModel):
    acknowledge_regressions: bool = False


def _record(result: dict, row: list) -> dict[str, Any]:
    return dict(zip(result["columns"], row, strict=True))


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


class SemanticViewService:
    @staticmethod
    def _parse(text: str, name: str) -> tuple[dict, SemanticModelIR]:
        try:
            parsed = parse_ossie(text)
        except OssieParseError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        definition = parsed.as_dict()
        if definition["name"] != name:
            raise HTTPException(
                status_code=422, detail="Semantic definition name must match view name"
            )
        try:
            return definition, SemanticModelIR.from_ossie(definition)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @staticmethod
    async def _get(view_id: str) -> dict | None:
        result = await db.execute_system(
            "SELECT id,catalog_name,database_name,schema_name,name,owner_name,"
            "active_version,status,created_at,updated_at "
            "FROM NOVA_SYSTEM.CONFIG_SEMANTIC_VIEWS WHERE id=%s",
            [view_id],
        )
        if not result["rows"]:
            return None
        row = _record(result, result["rows"][0])
        row["created_at"] = str(row["created_at"])
        row["updated_at"] = str(row["updated_at"])
        return row

    @staticmethod
    async def _version(view_id: str, version: int) -> dict | None:
        result = await db.execute_system(
            "SELECT view_id,version,definition,fingerprint,status,validation,created_at,"
            "validated_at,activated_at FROM NOVA_SYSTEM.CONFIG_SEMANTIC_VIEW_VERSIONS "
            "WHERE view_id=%s AND version=%s",
            [view_id, version],
        )
        if not result["rows"]:
            return None
        row = _record(result, result["rows"][0])
        row["definition"] = _json(row["definition"])
        row["validation"] = _json(row["validation"]) if row["validation"] else None
        for field in ("created_at", "validated_at", "activated_at"):
            row[field] = str(row[field]) if row[field] else None
        return row

    @staticmethod
    async def _source_access(definition: dict, user: dict) -> bool:
        for dataset in definition.get("datasets") or []:
            source = str(dataset.get("source") or "")
            if len(source.split(".")) not in (2, 3) or not all(
                _IDENT.fullmatch(part) for part in source.split(".")
            ):
                return False
            quoted = ".".join(f"`{part}`" for part in source.split("."))
            try:
                result = await query_service.execute(
                    sql=f"SELECT 1 FROM {quoted} WHERE 1=0",
                    username=user["username"],
                    encrypted_password=user["encrypted_password"],
                    database=source.split(".")[-2],
                    role=user.get("active_role"),
                    session_id=user.get("session_id"),
                    max_rows=0,
                )
                if result.error:
                    return False
            except Exception:
                return False
        return True

    @staticmethod
    async def _entity_access(ir: SemanticModelIR, user: dict) -> bool:
        for entity_id in ir.entity_ids:
            entity = await entity_registry.get(entity_id, user)
            if entity is None:
                return False
            matching = [dataset for dataset in ir.datasets if dataset.source == entity.relation]
            if not matching or not any(
                set(entity.key_columns).issubset({field.name for field in dataset.fields})
                and (not dataset.grain.keys or set(entity.key_columns) == set(dataset.grain.keys))
                for dataset in matching
            ):
                return False
        return True

    async def _visible(self, view_id: str, user: dict) -> dict:
        view = await self._get(view_id)
        if not view or view["status"] == "DEPRECATED":
            raise HTTPException(status_code=404, detail="Semantic View not found")
        version = view["active_version"]
        if version is None and can_manage(view["owner_name"], user):
            result = await db.execute_system(
                "SELECT MAX(version) FROM NOVA_SYSTEM.CONFIG_SEMANTIC_VIEW_VERSIONS "
                "WHERE view_id=%s",
                [view_id],
            )
            version = result["rows"][0][0]
        definition = await self._version(view_id, version) if version else None
        if not definition or not await self._source_access(definition["definition"], user):
            raise HTTPException(status_code=404, detail="Semantic View not found")
        return view

    async def list(self, user: dict) -> list[dict]:
        result = await db.execute_system(
            "SELECT id FROM NOVA_SYSTEM.CONFIG_SEMANTIC_VIEWS "
            "WHERE status<>'DEPRECATED' ORDER BY name"
        )
        visible = []
        for row in result["rows"]:
            try:
                visible.append(await self._visible(row[0], user))
            except HTTPException:
                continue
        return visible

    async def describe(self, view_id: str, user: dict) -> dict:
        view = await self._visible(view_id, user)
        result = await db.execute_system(
            "SELECT version FROM NOVA_SYSTEM.CONFIG_SEMANTIC_VIEW_VERSIONS "
            "WHERE view_id=%s ORDER BY version DESC",
            [view_id],
        )
        return {
            **view,
            "versions": [await self._version(view_id, row[0]) for row in result["rows"]],
        }

    async def create(self, body: SemanticViewCreate, user: dict) -> dict:
        definition, ir = self._parse(body.definition, body.name)
        if not await self._source_access(definition, user):
            raise HTTPException(status_code=403, detail="Semantic source is unavailable")
        if not await self._entity_access(ir, user):
            raise HTTPException(status_code=422, detail="Semantic entity reference is unavailable")
        scope = [body.catalog, body.database, body.schema_name, body.name]
        existing = await db.execute_system(
            "SELECT id FROM NOVA_SYSTEM.CONFIG_SEMANTIC_VIEWS WHERE catalog_name=%s "
            "AND database_name=%s AND schema_name=%s AND name=%s",
            scope,
        )
        if existing["rows"]:
            raise HTTPException(status_code=409, detail="Semantic View already exists")
        view_id = str(uuid4())
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_SEMANTIC_VIEWS "
            "(catalog_name,database_name,schema_name,name,id,owner_name,active_version,"
            "status,created_at,updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,NULL,'DRAFT',NOW(),NOW())",
            [*scope, view_id, user["username"]],
        )
        await self._insert_version(view_id, 1, definition, ir.fingerprint)
        await self._audit("CREATE", body.name, user)
        return await self.describe(view_id, user)

    @staticmethod
    async def _insert_version(view_id: str, version: int, definition: dict, fingerprint: str):
        await db.execute_system(
            "INSERT INTO NOVA_SYSTEM.CONFIG_SEMANTIC_VIEW_VERSIONS "
            "(view_id,version,definition,fingerprint,status,created_at) "
            "VALUES (%s,%s,%s,%s,'DRAFT',NOW())",
            [view_id, version, json.dumps(definition), fingerprint],
        )

    async def add_version(self, view_id: str, body: SemanticViewVersionCreate, user: dict):
        view = await self._owned(view_id, user)
        definition, ir = self._parse(body.definition, view["name"])
        if not await self._source_access(definition, user) or not await self._entity_access(
            ir, user
        ):
            raise HTTPException(status_code=403, detail="Semantic sources or entities unavailable")
        result = await db.execute_system(
            "SELECT MAX(version) FROM NOVA_SYSTEM.CONFIG_SEMANTIC_VIEW_VERSIONS WHERE view_id=%s",
            [view_id],
        )
        number = int(result["rows"][0][0] or 0) + 1
        await self._insert_version(view_id, number, definition, ir.fingerprint)
        await self._audit("ALTER", view["name"], user)
        return await self._version(view_id, number)

    async def _owned(self, view_id: str, user: dict) -> dict:
        view = await self._get(view_id)
        if not view or not can_manage(view["owner_name"], user):
            raise HTTPException(status_code=404, detail="Semantic View not found")
        return view

    @staticmethod
    async def _run_validation_query(sql: str, user: dict, database: str):
        result = await asyncio.wait_for(
            query_service.execute(
                sql=sql,
                username=user["username"],
                encrypted_password=user["encrypted_password"],
                database=database,
                role=user.get("active_role"),
                session_id=user.get("session_id"),
                max_rows=MAX_RESULT_ROWS,
            ),
            timeout=15,
        )
        if result.error:
            raise ValueError("Verified query execution failed")
        return result.columns, result.rows

    async def validate(self, view_id: str, version: int, user: dict) -> dict:
        view = await self._owned(view_id, user)
        row = await self._version(view_id, version)
        if not row or row["status"] not in {"DRAFT", "VALIDATED"}:
            raise HTTPException(status_code=409, detail="Semantic version cannot be validated")
        definition = row["definition"]
        ir = SemanticModelIR.from_ossie(definition)
        validation = validate_semantic_model_ir(ir)
        errors = list(validation.errors)
        if not await self._source_access(definition, user):
            errors.append("Source access is unavailable")
        if not await self._entity_access(ir, user):
            errors.append("Shared entity access is unavailable")
        verified = definition.get("verified_queries") or []
        if len(verified) > MAX_VERIFIED_QUERIES:
            errors.append(f"At most {MAX_VERIFIED_QUERIES} verified queries are supported")
        else:
            for item in verified:
                try:
                    plan = SemanticPlan.from_dict(item["semantic_plan"])
                    compiled = SemanticCompiler().compile(ir, plan)
                    verify_sql_compatibility(compiled.sql, item["verified_sql"])
                    await self._run_validation_query(compiled.sql, user, view["database_name"])
                except Exception:
                    errors.append("Verified query could not be compiled or executed")
        regression = None
        if view["active_version"]:
            active = await self._version(view_id, view["active_version"])
            if active:
                active_queries = active["definition"].get("verified_queries") or []
                if len(active_queries) > MAX_VERIFIED_QUERIES:
                    errors.append("Active version exceeds the verified query limit")
                else:
                    regression = await compare_semantic_versions(
                        definition, active["definition"], active_queries,
                        lambda sql: self._run_validation_query(
                            sql, user, view["database_name"]
                        ),
                    )
                    if any(case["status"] in {"execution_failed", "compile_failed"}
                           for case in regression["cases"]):
                        errors.append("Active verified queries could not be evaluated")
        report = {
            "valid": not errors,
            "errors": errors,
            "warnings": list(validation.warnings),
            "regression": regression,
            "fingerprint": ir.fingerprint,
            "baseline_version": view["active_version"],
        }
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_SEMANTIC_VIEW_VERSIONS SET validation=%s,"
            "status=%s,validated_at=NOW() WHERE view_id=%s AND version=%s",
            [json.dumps(report), "VALIDATED" if not errors else "DRAFT", view_id, version],
        )
        await self._audit("VALIDATE", view["name"], user)
        return report

    async def publish(
        self, view_id: str, version: int, user: dict,
        acknowledge_regressions: bool = False,
    ) -> dict:
        view = await self._owned(view_id, user)
        row = await self._version(view_id, version)
        if not row or row["status"] != "VALIDATED" or not row["validation"]["valid"]:
            raise HTTPException(status_code=409, detail="Only validated versions may be published")
        if row["validation"].get("baseline_version") != view["active_version"]:
            raise HTTPException(status_code=409, detail="Active version changed; validate again")
        regression = row["validation"].get("regression") or {}
        if regression.get("changed", 0) and not acknowledge_regressions:
            raise HTTPException(
                status_code=409,
                detail="Verified-query regressions require explicit acknowledgement",
            )
        if not await self._source_access(row["definition"], user):
            raise HTTPException(status_code=403, detail="Semantic source is unavailable")
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_SEMANTIC_VIEW_VERSIONS SET status='DEPRECATED' "
            "WHERE view_id=%s AND status='ACTIVE'",
            [view_id],
        )
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_SEMANTIC_VIEW_VERSIONS "
            "SET status='ACTIVE',activated_at=NOW() WHERE view_id=%s AND version=%s",
            [view_id, version],
        )
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_SEMANTIC_VIEWS "
            "SET active_version=%s,status='ACTIVE',updated_at=NOW() WHERE id=%s",
            [version, view_id],
        )
        await self._audit("PUBLISH", view["name"], user)
        return await self.describe(view_id, user)

    async def deprecate(self, view_id: str, user: dict) -> None:
        view = await self._owned(view_id, user)
        await db.execute_system(
            "UPDATE NOVA_SYSTEM.CONFIG_SEMANTIC_VIEWS "
            "SET status='DEPRECATED',active_version=NULL,updated_at=NOW() WHERE id=%s",
            [view_id],
        )
        await self._audit("DEPRECATE", view["name"], user)

    async def drop(self, view_id: str, user: dict) -> None:
        view = await self._owned(view_id, user)
        await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CONFIG_SEMANTIC_VIEW_VERSIONS WHERE view_id=%s",
            [view_id],
        )
        await db.execute_system(
            "DELETE FROM NOVA_SYSTEM.CONFIG_SEMANTIC_VIEWS WHERE id=%s", [view_id]
        )
        await self._audit("DROP", view["name"], user)

    async def query(self, view_id: str, body: SemanticViewQuery, user: dict) -> dict:
        view = await self._visible(view_id, user)
        version = body.version or view["active_version"]
        if not version:
            raise HTTPException(status_code=409, detail="Semantic View has no active version")
        row = await self._version(view_id, version)
        if not row or row["status"] not in {"ACTIVE", "DEPRECATED"}:
            raise HTTPException(status_code=404, detail="Semantic version not found")
        if not await self._source_access(row["definition"], user):
            raise HTTPException(status_code=403, detail="Semantic source is unavailable")
        ir = SemanticModelIR.from_ossie(row["definition"])
        plan = SemanticPlan.from_dict(
            {
                "metrics": body.metrics,
                "dimensions": body.dimensions,
                "filters": [
                    {"field": field, "operator": "=", "value": value}
                    for field, value in body.filters.items()
                ],
                "named_filters": body.named_filters,
                "limit": body.limit,
            }
        )
        compiled = SemanticCompiler().compile(ir, plan)
        result = await query_service.execute(
            sql=compiled.sql,
            username=user["username"],
            encrypted_password=user["encrypted_password"],
            database=view["database_name"],
            role=user.get("active_role"),
            session_id=user.get("session_id"),
            max_rows=body.limit,
        )
        if result.error:
            raise HTTPException(status_code=422, detail="Semantic query failed")
        await self._audit("QUERY", view["name"], user)
        return {
            "view_id": view_id,
            "version": version,
            "model_fingerprint": ir.fingerprint,
            "plan": plan.as_dict(),
            "sql": compiled.sql,
            "columns": result.columns,
            "rows": result.rows,
        }

    @staticmethod
    async def _audit(action: str, name: str, user: dict) -> None:
        await write_audit_log(
            event_type="SEMANTIC_VIEW",
            user_name=user["username"],
            action=action,
            object_type="SEMANTIC_VIEW",
            object_name=name,
            status="SUCCESS",
            session_id=user.get("session_id"),
            active_role=user.get("active_role"),
        )


semantic_view_service = SemanticViewService()


@router.get("", response_class=SanitizingJSONResponse)
async def list_semantic_views(user: CurrentUser):
    return await semantic_view_service.list(user)


@router.post("", status_code=201, response_class=SanitizingJSONResponse)
async def create_semantic_view(body: SemanticViewCreate, user: CurrentUser):
    return await semantic_view_service.create(body, user)


@router.get("/{view_id}", response_class=SanitizingJSONResponse)
async def describe_semantic_view(view_id: str, user: CurrentUser):
    return await semantic_view_service.describe(view_id, user)


@router.post("/{view_id}/versions", status_code=201, response_class=SanitizingJSONResponse)
async def add_semantic_version(view_id: str, body: SemanticViewVersionCreate, user: CurrentUser):
    return await semantic_view_service.add_version(view_id, body, user)


@router.post("/{view_id}/versions/{version}/validate", response_class=SanitizingJSONResponse)
async def validate_semantic_view(view_id: str, version: int, user: CurrentUser):
    return await semantic_view_service.validate(view_id, version, user)


@router.post("/{view_id}/versions/{version}/publish", response_class=SanitizingJSONResponse)
async def publish_semantic_view(
    view_id: str, version: int, user: CurrentUser,
    body: SemanticViewPublish | None = None,
):
    return await semantic_view_service.publish(
        view_id, version, user,
        acknowledge_regressions=body.acknowledge_regressions if body else False,
    )


@router.post("/{view_id}/query", response_class=SanitizingJSONResponse)
async def query_semantic_view(view_id: str, body: SemanticViewQuery, user: CurrentUser):
    return await semantic_view_service.query(view_id, body, user)


@router.post("/{view_id}/deprecate", status_code=204)
async def deprecate_semantic_view(view_id: str, user: CurrentUser):
    await semantic_view_service.deprecate(view_id, user)


@router.delete("/{view_id}", status_code=204)
async def drop_semantic_view(view_id: str, user: CurrentUser):
    await semantic_view_service.drop(view_id, user)
