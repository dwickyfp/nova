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
from app.common.sql_guard import split_sql_statements
from app.core.database import db
from app.core.deps import get_current_user
from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.ossie import SUPPORTED_VERSIONS, OssieParseError, parse_ossie
from app.modules.agents.semantic.planning import SemanticPlan, SemanticPlanner
from app.modules.agents.semantic.quality_lab import evaluate_verified_queries
from app.modules.agents.semantic.runtime import (
    lint_semantic_model,
    semantic_quality,
    validate_semantic_model_ir,
)
from app.modules.agents.semantic.verification import verify_sql_compatibility
from app.modules.assistant.skills import contains_credential_shape
from app.modules.assistant.tools import policy
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


class SemanticViewPreview(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


class SemanticViewVerifiedQueryCreate(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    semantic_plan: dict
    verified_sql: str = Field(min_length=1, max_length=100_000)
    expected_result_signature: str | None = Field(default=None, max_length=256)
    tags: list[str] = Field(default_factory=list, max_length=32)


def _record(result: dict, row: list) -> dict[str, Any]:
    return dict(zip(result["columns"], row, strict=True))


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _legacy_review_draft(view: dict, row: dict, user: dict) -> bool:
    migration = (row.get("validation") or {}).get("migration") or {}
    return (
        view.get("visibility") == "PRIVATE"
        and view.get("status") == "DRAFT"
        and view.get("owner_name") == user.get("username")
        and row.get("status") == "DRAFT"
        and migration.get("source") == "CONFIG_SEMANTIC_MODELS"
        and not (row.get("validation") or {}).get("valid")
    )


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
            "visibility,active_version,status,created_at,updated_at "
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
        if not user.get("username") or not user.get("encrypted_password"):
            return False
        for dataset in definition.get("datasets") or []:
            source = str(dataset.get("source") or "")
            if len(source.split(".")) not in (2, 3) or not all(
                _IDENT.fullmatch(part) for part in source.split(".")
            ):
                return False
            quoted = ".".join(f"`{part}`" for part in source.split("."))
            try:
                result = await asyncio.wait_for(
                    query_service.execute(
                        sql=f"SELECT 1 FROM {quoted} WHERE 1=0",
                        username=user["username"],
                        encrypted_password=user["encrypted_password"],
                        database=source.split(".")[-2],
                        role=user.get("active_role"),
                        session_id=user.get("session_id"),
                        max_rows=0,
                    ),
                    timeout=5,
                )
                if result.error:
                    return False
            except Exception:
                return False
        return True

    @staticmethod
    async def _entity_access(ir: SemanticModelIR, user: dict) -> bool:
        for entity_id in ir.entity_ids:
            try:
                entity = await entity_registry.get(entity_id, user)
            except Exception:
                return False
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
        if view.get("visibility") == "PRIVATE" and view["owner_name"] != user.get("username"):
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
        if not definition:
            raise HTTPException(status_code=404, detail="Semantic View not found")
        if _legacy_review_draft(view, definition, user):
            return view
        try:
            ir = SemanticModelIR.from_ossie(definition["definition"])
            accessible = (
                bool(ir.datasets)
                and await self._source_access(definition["definition"], user)
                and await self._entity_access(ir, user)
            )
        except (TypeError, ValueError, KeyError):
            accessible = False
        if not accessible:
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
        versions = [await self._version(view_id, row[0]) for row in result["rows"]]
        if not can_manage(view["owner_name"], user):
            accessible_versions = []
            for row in versions:
                if not row or row["status"] not in {"ACTIVE", "DEPRECATED"}:
                    continue
                try:
                    ir = SemanticModelIR.from_ossie(row["definition"])
                    if (
                        ir.datasets
                        and await self._source_access(row["definition"], user)
                        and await self._entity_access(ir, user)
                    ):
                        accessible_versions.append(row)
                except (TypeError, ValueError, KeyError):
                    continue
            versions = accessible_versions
        return {
            **view,
            "versions": versions,
        }

    @staticmethod
    async def _private_agent_access(view: dict, user: dict, agent_id: str | None) -> bool:
        if view.get("visibility") != "PRIVATE" or view["owner_name"] == user.get("username"):
            return True
        if not agent_id or not user.get("active_role"):
            return False
        from app.modules.agents.repository import agent_repository
        from app.modules.agents.semantic.access import bound_view_ids

        agent = await agent_repository.get_shared_agent(
            agent_id, role_name=str(user["active_role"])
        )
        if not agent or agent.get("owner_name") != view["owner_name"]:
            return False
        return view["id"] in bound_view_ids(agent)

    async def get_active_for_agent(
        self, view_id: str, user: dict, *, agent_id: str | None = None
    ) -> dict | None:
        """Resolve one published definition under the caller's current role."""
        try:
            view = await self._get(view_id)
            if not view or view["status"] != "ACTIVE" or not view["active_version"]:
                return None
            if not await self._private_agent_access(view, user, agent_id):
                return None
            row = await self._version(view_id, int(view["active_version"]))
            if not row or row["status"] != "ACTIVE":
                return None
            definition = row["definition"]
            ir = SemanticModelIR.from_ossie(definition)
            if not ir.datasets or row["fingerprint"] != ir.fingerprint:
                return None
            if not await self._source_access(definition, user):
                return None
            if not await self._entity_access(ir, user):
                return None
        except Exception:
            return None
        return {
            "id": view_id,
            "semantic_model_id": view_id,
            "name": view["name"],
            "owner_name": view["owner_name"],
            "visibility": view.get("visibility") or "PUBLIC",
            "status": "ACTIVE",
            "version": int(view["active_version"]),
            "definition": definition,
            "fingerprint": row["fingerprint"],
            "database_name": view["database_name"],
        }

    async def list_active_for_agent(
        self, user: dict, *, agent_id: str | None = None
    ) -> list[dict]:
        result = await db.execute_system(
            "SELECT id FROM NOVA_SYSTEM.CONFIG_SEMANTIC_VIEWS "
            "WHERE status='ACTIVE' AND active_version IS NOT NULL ORDER BY name"
        )
        visible = []
        for row in result["rows"]:
            definition = await self.get_active_for_agent(str(row[0]), user, agent_id=agent_id)
            if definition:
                visible.append(definition)
        return visible

    async def _readable_version(self, view_id: str, version: int, user: dict) -> tuple[dict, dict]:
        view = await self._get(view_id)
        if not view or view["status"] == "DEPRECATED":
            raise HTTPException(status_code=404, detail="Semantic View not found")
        if view.get("visibility") == "PRIVATE" and view["owner_name"] != user.get("username"):
            raise HTTPException(status_code=404, detail="Semantic View not found")
        row = await self._version(view_id, version)
        if not row or (
            row["status"] not in {"ACTIVE", "DEPRECATED"}
            and not can_manage(view["owner_name"], user)
        ):
            raise HTTPException(status_code=404, detail="Semantic version not found")
        if _legacy_review_draft(view, row, user):
            return view, row
        try:
            ir = SemanticModelIR.from_ossie(row["definition"])
            allowed = bool(ir.datasets) and await self._source_access(row["definition"], user)
            entities_allowed = await self._entity_access(ir, user)
        except (TypeError, ValueError, KeyError):
            allowed = entities_allowed = False
        if not allowed or not entities_allowed:
            raise HTTPException(status_code=404, detail="Semantic version not found")
        return view, row

    async def preview(self, view_id: str, version: int, question: str, user: dict) -> dict:
        view, row = await self._readable_version(view_id, version, user)
        if str(row["definition"].get("version") or "") not in SUPPORTED_VERSIONS:
            raise HTTPException(
                status_code=422,
                detail="Replace this legacy draft with a supported Ossie 0.1.1 definition",
            )
        ir = SemanticModelIR.from_ossie(row["definition"])
        planned = SemanticPlanner().plan(ir, question)
        if planned.plan is None:
            raise HTTPException(
                status_code=422,
                detail=planned.clarification or "The semantic question is ambiguous.",
            )
        try:
            compiled = SemanticCompiler().compile(ir, planned.plan)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        preview = {
            "view_id": view_id,
            "version": version,
            "model_fingerprint": row["fingerprint"],
            "semantic_plan": planned.plan.as_dict(),
            "generated_sql": compiled.sql,
            "confidence": {
                "score": planned.confidence.score,
                "level": planned.confidence.level,
                "signals": planned.confidence.signals,
                "unresolved": list(planned.confidence.unresolved),
            },
            "relationship_path": list(compiled.relationship_path),
            "warnings": list(compiled.warnings),
        }
        await self._audit("PREVIEW", view["name"], user)
        return preview

    async def quality(self, view_id: str, version: int, user: dict) -> dict:
        view, row = await self._readable_version(view_id, version, user)
        if str(row["definition"].get("version") or "") not in SUPPORTED_VERSIONS:
            migration = (row.get("validation") or {}).get("migration") or {}
            verified = migration.get("legacy_verified_queries") or []
            report = {
                "view_id": view_id,
                "version": version,
                "model_fingerprint": row["fingerprint"],
                "valid": False,
                "errors": (row.get("validation") or {}).get("errors") or [],
                "findings": [],
                "quality": {"verified_query_count": len(verified)},
                "verified_queries": verified,
                "quality_lab": {
                    "model_fingerprint": row["fingerprint"],
                    "total": 0,
                    "matched": 0,
                    "changed": 0,
                    "cases": [],
                    "unevaluated": len(verified),
                },
            }
            await self._audit("QUALITY", view["name"], user)
            return report
        ir = SemanticModelIR.from_ossie(row["definition"])
        validation = validate_semantic_model_ir(ir)
        verified = []
        for index, item in enumerate(row["definition"].get("verified_queries") or [], 1):
            if isinstance(item, dict):
                entry = dict(item)
                entry["verified_query_id"] = str(
                    item.get("verified_query_id") or f"{view_id}:v{version}:{index}"
                )
                entry["question"] = str(item.get("question") or "")
                entry["semantic_plan"] = item.get("semantic_plan") or {}
                entry["verified_sql"] = str(item.get("verified_sql") or "")
                verified.append(entry)
        quality_lab = evaluate_verified_queries(row["definition"], verified)
        quality_lab["unevaluated"] = max(0, len(verified) - quality_lab["total"])
        report = {
            "view_id": view_id,
            "version": version,
            "model_fingerprint": row["fingerprint"],
            "valid": validation.valid,
            "errors": list(validation.errors),
            "findings": [item.__dict__ for item in lint_semantic_model(ir)],
            "quality": semantic_quality(ir, verified_query_count=len(verified)),
            "verified_queries": verified,
            "quality_lab": quality_lab,
        }
        await self._audit("QUALITY", view["name"], user)
        return report

    async def add_verified_query(
        self,
        view_id: str,
        base_version: int,
        body: SemanticViewVerifiedQueryCreate,
        user: dict,
    ) -> dict:
        view = await self._owned(view_id, user)
        _, base = await self._readable_version(view_id, base_version, user)
        result = await db.execute_system(
            "SELECT MAX(version) FROM NOVA_SYSTEM.CONFIG_SEMANTIC_VIEW_VERSIONS WHERE view_id=%s",
            [view_id],
        )
        latest_version = int(result["rows"][0][0] or 0)
        if latest_version != base_version:
            raise HTTPException(status_code=409, detail="A newer Semantic View version exists")
        definition = json.loads(json.dumps(base["definition"]))
        verified = definition.get("verified_queries") or []
        if len(verified) >= MAX_VERIFIED_QUERIES:
            raise HTTPException(
                status_code=422,
                detail=f"At most {MAX_VERIFIED_QUERIES} verified queries are supported",
            )
        if contains_credential_shape(body.model_dump_json()):
            raise HTTPException(status_code=422, detail="Verified query cannot contain credentials")
        statements = split_sql_statements(body.verified_sql)
        classification, decisions = policy.classify_statements(statements)
        if classification != "read_only" or len(statements) != 1 or len(decisions) != 1:
            raise HTTPException(status_code=422, detail="Verified SQL must be one read-only query")
        try:
            ir = SemanticModelIR.from_ossie(definition)
            plan = SemanticPlan.from_dict(body.semantic_plan)
            compiled = SemanticCompiler().compile(ir, plan)
            verify_sql_compatibility(compiled.sql, body.verified_sql)
        except (TypeError, KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Verified query is incompatible") from exc
        verified.append(
            {
                "verified_query_id": str(uuid4()),
                "question": body.question,
                "semantic_plan": plan.as_dict(),
                "verified_sql": body.verified_sql,
                "expected_result_signature": body.expected_result_signature,
                "tags": body.tags,
                "verified_by": user["username"],
            }
        )
        definition["verified_queries"] = verified
        new_ir = SemanticModelIR.from_ossie(definition)
        await self._insert_version(view_id, latest_version + 1, definition, new_ir.fingerprint)
        await self._audit("ALTER", view["name"], user)
        return await self._version(view_id, latest_version + 1)

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
            "status,visibility,created_at,updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,NULL,'DRAFT','PUBLIC',NOW(),NOW())",
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
        if (
            not view
            or (view.get("visibility") == "PRIVATE" and view["owner_name"] != user.get("username"))
            or not can_manage(view["owner_name"], user)
        ):
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
        if str(definition.get("version") or "") not in SUPPORTED_VERSIONS:
            raise HTTPException(
                status_code=422,
                detail="Replace this legacy draft with a supported Ossie 0.1.1 definition",
            )
        ir = SemanticModelIR.from_ossie(definition)
        validation = validate_semantic_model_ir(ir)
        errors = list(validation.errors)
        warnings = list(validation.warnings)
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
                regression = await compare_semantic_versions(
                    definition, active["definition"], active_queries[:MAX_VERIFIED_QUERIES],
                    lambda sql: self._run_validation_query(
                        sql, user, view["database_name"]
                    ),
                )
                excess = active_queries[MAX_VERIFIED_QUERIES:]
                if excess:
                    warnings.append(
                        f"{len(excess)} imported baseline verified queries exceed the "
                        "regression limit and require acknowledgement before publishing"
                    )
                    regression["cases"].extend(
                        {
                            "verified_query_id": str(item.get("verified_query_id") or ""),
                            "question": str(item.get("question") or ""),
                            "status": "not_evaluated",
                        }
                        for item in excess if isinstance(item, dict)
                    )
                    regression["total"] = len(active_queries)
                    regression["changed"] += len(excess)
                if any(case["status"] in {"execution_failed", "compile_failed"}
                       for case in regression["cases"]):
                    errors.append("Active verified queries could not be evaluated")
        report = {
            "valid": not errors,
            "errors": errors,
            "warnings": warnings,
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

    async def query(
        self, view_id: str, body: SemanticViewQuery, user: dict, *, agent_id: str | None = None
    ) -> dict:
        if agent_id:
            if not await self.get_active_for_agent(view_id, user, agent_id=agent_id):
                raise HTTPException(status_code=404, detail="Semantic View not found")
            view = await self._get(view_id)
            assert view is not None
        else:
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
        if not await self._entity_access(ir, user):
            raise HTTPException(status_code=403, detail="Semantic entity reference is unavailable")
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


@router.post("/{view_id}/versions/{version}/preview", response_class=SanitizingJSONResponse)
async def preview_semantic_view(
    view_id: str, version: int, body: SemanticViewPreview, user: CurrentUser
):
    return await semantic_view_service.preview(view_id, version, body.question, user)


@router.get("/{view_id}/versions/{version}/quality", response_class=SanitizingJSONResponse)
async def quality_semantic_view(view_id: str, version: int, user: CurrentUser):
    return await semantic_view_service.quality(view_id, version, user)


@router.post(
    "/{view_id}/versions/{version}/verified-queries",
    status_code=201,
    response_class=SanitizingJSONResponse,
)
async def add_semantic_view_verified_query(
    view_id: str, version: int, body: SemanticViewVerifiedQueryCreate, user: CurrentUser
):
    return await semantic_view_service.add_verified_query(view_id, version, body, user)


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


# Rule proposals are authored in Agent Studio, but their governed object is a
# Semantic View. Reuse the handlers so both URL families share one lifecycle.
from app.modules.agents.router import (  # noqa: E402
    approve_rule_proposal,
    create_rule_proposal,
    list_rule_proposals,
    preview_rule_proposal,
    reject_rule_proposal,
)
from app.modules.agents.schemas import (  # noqa: E402
    RuleProposalListResponse,
    RuleProposalPreviewResponse,
    RuleProposalView,
)

router.add_api_route(
    "/{model_id}/rule-proposals",
    create_rule_proposal,
    methods=["POST"],
    response_model=RuleProposalView,
    status_code=201,
)
router.add_api_route(
    "/{model_id}/rule-proposals",
    list_rule_proposals,
    methods=["GET"],
    response_model=RuleProposalListResponse,
)
router.add_api_route(
    "/{model_id}/rule-proposals/{proposal_id}/preview",
    preview_rule_proposal,
    methods=["POST"],
    response_model=RuleProposalPreviewResponse,
)
router.add_api_route(
    "/{model_id}/rule-proposals/{proposal_id}/approve",
    approve_rule_proposal,
    methods=["POST"],
    response_model=RuleProposalView,
)
router.add_api_route(
    "/{model_id}/rule-proposals/{proposal_id}/reject",
    reject_rule_proposal,
    methods=["POST"],
    response_model=RuleProposalView,
)
