"""Copy legacy Agent Studio semantic models into versioned Semantic Views.

The legacy tables remain intact. An imported model keeps its UUID, so existing
agent bindings can resolve the published view without a second mapping table.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Any

from app.common.audit import write_audit_log
from app.common.sql_guard import split_sql_statements
from app.core.database import db
from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.ossie import SUPPORTED_VERSIONS
from app.modules.agents.semantic.planning import SemanticPlan
from app.modules.agents.semantic.runtime import validate_semantic_model_ir
from app.modules.agents.semantic.verification import verify_sql_compatibility
from app.modules.assistant.skills import contains_credential_shape
from app.modules.assistant.tools import policy
from app.modules.intelligence.semantic_regression import MAX_VERIFIED_QUERIES

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def _value(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _view_name(value: str, model_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value).strip("_")[:112]
    if not cleaned:
        return f"legacy_{re.sub(r'[^A-Za-z0-9]', '', model_id)[:16]}"
    if cleaned[0].isdigit():
        cleaned = f"semantic_{cleaned}"[:112]
    return cleaned


def _database(model: dict, definition: dict) -> str | None:
    value = str(model.get("database_name") or "")
    if not value:
        for dataset in definition.get("datasets") or []:
            if not isinstance(dataset, dict):
                continue
            parts = str(dataset.get("source") or "").split(".")
            if len(parts) in (2, 3):
                value = parts[-2]
                break
    return value if _IDENT.fullmatch(value) else None


def _legacy_verified_query(row: dict) -> dict:
    return {
        "verified_query_id": str(row["verified_query_id"]),
        "question": row["question"],
        "semantic_plan": _value(row["semantic_plan"]),
        "verified_sql": row["verified_sql"],
        "expected_result_signature": row["expected_result_signature"],
        "verified_by": row["verified_by"],
        "verified_at": str(row["verified_at"]),
        "tags": _value(row["tags"]) or [],
        "usage_count": int(row["usage_count"] or 0),
        "success_count": int(row["success_count"] or 0),
        "legacy_model_fingerprint": row["model_fingerprint"],
    }


async def migrate_legacy_semantic_models() -> dict[str, Any]:
    """Idempotently import legacy metadata; return IDs needing human review."""
    models_result = await db.execute_system(
        "SELECT semantic_model_id,owner_name,name,description,database_name,"
        "schema_name,ossie_version,definition,source_file_id,created_at,updated_at "
        "FROM NOVA_SYSTEM.CONFIG_SEMANTIC_MODELS ORDER BY semantic_model_id"
    )
    verified_result = await db.execute_system(
        "SELECT verified_query_id,owner_name,semantic_model_id,model_fingerprint,"
        "question,semantic_plan,verified_sql,expected_result_signature,verified_by,"
        "verified_at,tags,usage_count,success_count "
        "FROM NOVA_SYSTEM.CONFIG_SEMANTIC_VERIFIED_QUERIES ORDER BY verified_query_id"
    )
    models = [
        dict(zip(models_result["columns"], row, strict=True))
        for row in models_result["rows"]
    ]
    verified_by_model: dict[tuple[str, str], list[dict]] = defaultdict(list)
    invalid_verified_models: set[tuple[str, str]] = set()
    for row in verified_result["rows"]:
        record = dict(zip(verified_result["columns"], row, strict=True))
        key = (str(record["semantic_model_id"]), str(record["owner_name"]))
        try:
            verified_by_model[key].append(_legacy_verified_query(record))
        except (TypeError, ValueError, KeyError):
            invalid_verified_models.add(key)

    report: dict[str, Any] = {
        "imported": 0,
        "already_present": 0,
        "drafts_needing_review": [],
        "unresolved": [],
    }
    seen_models: set[tuple[str, str]] = set()
    for model in models:
        model_id = str(model["semantic_model_id"])
        owner = str(model["owner_name"])
        seen_models.add((model_id, owner))
        try:
            if (model_id, owner) in invalid_verified_models:
                raise ValueError("a legacy verified query cannot be decoded")
            original = _value(model["definition"])
            if not isinstance(original, dict):
                raise ValueError("definition is not an Ossie mapping")
            legacy_version = str(original.get("version") or model.get("ossie_version") or "")
            supported = str(original.get("version") or "") in SUPPORTED_VERSIONS
            if contains_credential_shape(json.dumps(original)):
                raise ValueError("legacy definition contains a credential-shaped value")
            raw_definition = json.loads(json.dumps(original))
            definition = json.loads(json.dumps(original))
            if supported and (
                not isinstance(definition.get("datasets"), list) or not definition["datasets"]
            ):
                raise ValueError("legacy definition has no datasets")
            database = _database(model, definition)
            if database is None:
                if supported:
                    raise ValueError("database scope cannot be determined")
                database = "NOVA_SYSTEM"
            schema_name = str(model.get("schema_name") or "")
            if len(schema_name) > 128:
                raise ValueError("schema scope is too long")
            verified = list(verified_by_model[(model_id, owner)])
            if supported:
                verified = []
                seen_verified_ids: set[str] = set()
                for index, item in enumerate(definition.get("verified_queries") or [], 1):
                    if not isinstance(item, dict):
                        raise ValueError("embedded verified query is not a mapping")
                    entry = dict(item)
                    entry.setdefault("verified_query_id", f"{model_id}:embedded:{index}")
                    seen_verified_ids.add(str(entry["verified_query_id"]))
                    verified.append(entry)
                for entry in verified_by_model[(model_id, owner)]:
                    if entry["verified_query_id"] not in seen_verified_ids:
                        verified.append(entry)
                        seen_verified_ids.add(entry["verified_query_id"])
            elif isinstance(definition.get("verified_queries"), list):
                embedded = list(definition["verified_queries"])
                seen_verified_ids = {
                    str(item.get("verified_query_id"))
                    for item in embedded if isinstance(item, dict) and item.get("verified_query_id")
                }
                verified = embedded + [
                    item for item in verified
                    if item["verified_query_id"] not in seen_verified_ids
                ]
            if contains_credential_shape(json.dumps(verified, default=str)):
                raise ValueError("legacy verified query contains a credential-shaped value")
            existing = await db.execute_system(
                "SELECT name,catalog_name,database_name,schema_name,active_version,status "
                "FROM NOVA_SYSTEM.CONFIG_SEMANTIC_VIEWS WHERE id=%s",
                [model_id],
            )
            if existing["rows"]:
                existing_name, catalog, database, schema_name, active_version, status = (
                    existing["rows"][0]
                )
                if active_version is not None or status != "DRAFT":
                    report["already_present"] += 1
                    continue
                current_version = await db.execute_system(
                    "SELECT validation FROM NOVA_SYSTEM.CONFIG_SEMANTIC_VIEW_VERSIONS "
                    "WHERE view_id=%s AND version=1",
                    [model_id],
                )
                if current_version["rows"]:
                    validation = _value(current_version["rows"][0][0]) or {}
                    if validation.get("migration", {}).get("source") != "CONFIG_SEMANTIC_MODELS":
                        report["unresolved"].append(
                            {
                                "model_id": model_id,
                                "reason": "existing view ID is not a legacy import",
                            }
                        )
                        continue
                    if validation.get("valid"):
                        await db.execute_system(
                            "UPDATE NOVA_SYSTEM.CONFIG_SEMANTIC_VIEWS "
                            "SET active_version=1,status='ACTIVE',updated_at=NOW() WHERE id=%s",
                            [model_id],
                        )
                    else:
                        report["drafts_needing_review"].append(model_id)
                    report["already_present"] += 1
                    continue
                name = str(existing_name)
            else:
                catalog = "default_catalog"
                original_name = str(model.get("name") or definition.get("name") or "")
                name = _view_name(original_name, model_id)
                scope = [catalog, database, schema_name, name]
                collision = await db.execute_system(
                    "SELECT id FROM NOVA_SYSTEM.CONFIG_SEMANTIC_VIEWS "
                    "WHERE catalog_name=%s AND database_name=%s AND schema_name=%s AND name=%s",
                    scope,
                )
                if collision["rows"]:
                    suffix = re.sub(r"[^A-Za-z0-9]", "", model_id)[:8]
                    name = f"{name[:103]}_{suffix}"
                    scope[-1] = name
                    collision = await db.execute_system(
                        "SELECT id FROM NOVA_SYSTEM.CONFIG_SEMANTIC_VIEWS "
                        "WHERE catalog_name=%s AND database_name=%s AND schema_name=%s "
                        "AND name=%s",
                        scope,
                    )
                    if collision["rows"]:
                        raise ValueError("semantic name collision could not be resolved")
            if supported:
                definition["name"] = name
                if model.get("description") is not None:
                    definition["description"] = str(model["description"])
                definition["verified_queries"] = verified
                ir = SemanticModelIR.from_ossie(definition)
                validation = validate_semantic_model_ir(ir)
                errors = list(validation.errors)
                for dataset in ir.datasets:
                    parts = dataset.source.split(".")
                    if len(parts) not in (2, 3) or not all(
                        _IDENT.fullmatch(part) for part in parts
                    ):
                        errors.append("A legacy dataset has an invalid source reference")
                        break
                for item in verified:
                    try:
                        statements = split_sql_statements(item["verified_sql"])
                        classification, decisions = policy.classify_statements(statements)
                        if (
                            classification != "read_only"
                            or len(statements) != 1
                            or len(decisions) != 1
                        ):
                            raise ValueError("verified SQL is not read-only")
                        plan = SemanticPlan.from_dict(item["semantic_plan"])
                        compiled = SemanticCompiler().compile(ir, plan)
                        verify_sql_compatibility(compiled.sql, item["verified_sql"])
                    except (KeyError, TypeError, ValueError):
                        errors.append(
                            "A legacy verified query is incompatible with this definition"
                        )
                        break
                warnings = list(validation.warnings)
                fingerprint = ir.fingerprint
            else:
                definition["verified_queries"] = verified
                errors = [
                    f"Unsupported legacy Ossie version {legacy_version or '(missing)'}. "
                    "Replace this draft with a supported 0.1.1 definition."
                ]
                warnings = ["Original legacy definition is preserved for owner review."]
                canonical = json.dumps(definition, sort_keys=True, separators=(",", ":"))
                fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
            warnings.append(
                "Imported from Agent Studio without executing source or verified queries; "
                "caller access is checked at read time."
            )
            if len(verified) > MAX_VERIFIED_QUERIES:
                warnings.append(
                    f"Legacy model contains {len(verified)} verified queries; new versions "
                    f"must keep at most {MAX_VERIFIED_QUERIES}."
                )
            report_data = {
                "valid": not errors,
                "errors": errors,
                "warnings": warnings,
                "fingerprint": fingerprint,
                "baseline_version": None,
                "migration": {
                    "source": "CONFIG_SEMANTIC_MODELS",
                    "legacy_id": model_id,
                    "legacy_name": str(model.get("name") or ""),
                    "legacy_version": legacy_version,
                    "source_file_id": model.get("source_file_id"),
                    "verified_query_count": len(verified),
                    "legacy_verified_queries": verified if not supported else [],
                    "raw_definition": raw_definition if not supported else None,
                    "raw_definition_preserved": not supported,
                    "source_checks_executed": False,
                },
            }
            status = "ACTIVE" if not errors else "DRAFT"
            if not existing["rows"]:
                await db.execute_system(
                    "INSERT INTO NOVA_SYSTEM.CONFIG_SEMANTIC_VIEWS "
                    "(catalog_name,database_name,schema_name,name,id,owner_name,"
                    "active_version,status,visibility,created_at,updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,NULL,'DRAFT','PRIVATE',%s,%s)",
                    [catalog, database, schema_name, name, model_id, owner,
                     model["created_at"], model["updated_at"]],
                )
            await db.execute_system(
                "INSERT INTO NOVA_SYSTEM.CONFIG_SEMANTIC_VIEW_VERSIONS "
                "(view_id,version,definition,fingerprint,status,validation,created_at,"
                "validated_at,activated_at) VALUES (%s,1,%s,%s,%s,%s,%s,%s,%s)",
                [model_id, json.dumps(definition), fingerprint, status,
                 json.dumps(report_data), model["created_at"],
                 model["updated_at"] if not errors else None,
                 model["updated_at"] if not errors else None],
            )
            if not errors:
                await db.execute_system(
                    "UPDATE NOVA_SYSTEM.CONFIG_SEMANTIC_VIEWS "
                    "SET active_version=1,status='ACTIVE',updated_at=NOW() WHERE id=%s",
                    [model_id],
                )
            else:
                report["drafts_needing_review"].append(model_id)
            await write_audit_log(
                event_type="SEMANTIC_VIEW",
                user_name="system",
                action="MIGRATE",
                object_type="SEMANTIC_VIEW",
                object_name=name,
                status="SUCCESS",
            )
            report["imported"] += 1
        except (TypeError, ValueError, KeyError) as exc:
            report["unresolved"].append({"model_id": model_id, "reason": str(exc)})

    for model_id, _owner in (verified_by_model.keys() | invalid_verified_models) - seen_models:
        report["unresolved"].append(
            {"model_id": model_id, "reason": "verified queries have no legacy model"}
        )
    return report
