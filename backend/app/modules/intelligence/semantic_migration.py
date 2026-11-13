"""Copy legacy Agent Studio semantic models into versioned Semantic Views.

The legacy tables are read-only. An imported model keeps its UUID, so existing
agent bindings can resolve the published view without a second mapping table.

The import runs as the ``semantic-migration`` service principal, so each view
is that principal's private draft (the metadata store makes a new view its
creator's). The legacy owner and timestamps are recorded in the version's
migration report rather than on the view row.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime
from typing import Any

from app.common.audit import write_audit_log
from app.common.sql_guard import split_sql_statements
from app.core.metadata_db import MetadataConflict, MetadataIntegrityViolation
from app.modules.agents.semantic.compiler import SemanticCompiler
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.semantic.ossie import SUPPORTED_VERSIONS
from app.modules.agents.semantic.planning import SemanticPlan
from app.modules.agents.semantic.runtime import validate_semantic_model_ir
from app.modules.agents.semantic.verification import verify_sql_compatibility
from app.modules.assistant.skills import contains_credential_shape
from app.modules.assistant.tools import policy
from app.modules.intelligence.metadata_repository import (
    IntelligenceMetadataRepository,
    intelligence_metadata,
    service_actor,
)
from app.modules.intelligence.semantic_regression import MAX_VERIFIED_QUERIES

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
MIGRATION_SERVICE = "semantic-migration"
_SOURCE = "CONFIG_SEMANTIC_MODELS"


def _value(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _time(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)


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
        "verified_at": _time(row["verified_at"]),
        "tags": _value(row["tags"]) or [],
        "usage_count": int(row["usage_count"] or 0),
        "success_count": int(row["success_count"] or 0),
        "legacy_model_fingerprint": row["model_fingerprint"],
    }


def _verified_queries(
    model_id: str, definition: dict, legacy: list[dict], *, supported: bool,
) -> list[dict]:
    """Embedded verified queries first, then legacy rows not already embedded."""
    if supported:
        verified: list[dict] = []
        seen: set[str] = set()
        for index, item in enumerate(definition.get("verified_queries") or [], 1):
            if not isinstance(item, dict):
                raise ValueError("embedded verified query is not a mapping")
            entry = dict(item)
            entry.setdefault("verified_query_id", f"{model_id}:embedded:{index}")
            seen.add(str(entry["verified_query_id"]))
            verified.append(entry)
        for entry in legacy:
            if entry["verified_query_id"] not in seen:
                verified.append(entry)
                seen.add(entry["verified_query_id"])
        return verified
    if isinstance(definition.get("verified_queries"), list):
        embedded = list(definition["verified_queries"])
        seen = {
            str(item.get("verified_query_id"))
            for item in embedded if isinstance(item, dict) and item.get("verified_query_id")
        }
        return embedded + [item for item in legacy if item["verified_query_id"] not in seen]
    return list(legacy)


def _validate(
    definition: dict, verified: list[dict], *, supported: bool, legacy_version: str,
) -> tuple[list[str], list[str], str]:
    """Validate a supported definition offline; return errors, warnings, fingerprint."""
    if not supported:
        canonical = json.dumps(definition, sort_keys=True, separators=(",", ":"))
        return (
            [f"Unsupported legacy Ossie version {legacy_version or '(missing)'}. "
             "Replace this draft with a supported 0.1.1 definition."],
            ["Original legacy definition is preserved for owner review."],
            hashlib.sha256(canonical.encode()).hexdigest(),
        )
    ir = SemanticModelIR.from_ossie(definition)
    validation = validate_semantic_model_ir(ir)
    errors = list(validation.errors)
    for dataset in ir.datasets:
        parts = dataset.source.split(".")
        if len(parts) not in (2, 3) or not all(_IDENT.fullmatch(part) for part in parts):
            errors.append("A legacy dataset has an invalid source reference")
            break
    for item in verified:
        try:
            statements = split_sql_statements(item["verified_sql"])
            classification, decisions = policy.classify_statements(statements)
            if classification != "read_only" or len(statements) != 1 or len(decisions) != 1:
                raise ValueError("verified SQL is not read-only")
            plan = SemanticPlan.from_dict(item["semantic_plan"])
            compiled = SemanticCompiler().compile(ir, plan)
            verify_sql_compatibility(compiled.sql, item["verified_sql"])
        except (KeyError, TypeError, ValueError):
            errors.append("A legacy verified query is incompatible with this definition")
            break
    return errors, list(validation.warnings), ir.fingerprint


async def _resolve_name(
    repo: IntelligenceMetadataRepository, catalog: str, database: str, schema_name: str,
    name: str, model_id: str,
) -> str:
    if await repo.find_view(catalog, database, schema_name, name) is None:
        return name
    suffix = re.sub(r"[^A-Za-z0-9]", "", model_id)[:8]
    name = f"{name[:103]}_{suffix}"
    if await repo.find_view(catalog, database, schema_name, name) is not None:
        raise ValueError("semantic name collision could not be resolved")
    return name


async def _import_model(
    repo: IntelligenceMetadataRepository, model: dict, legacy_verified: list[dict],
    report: dict[str, Any],
) -> str | None:
    """Import one model in the caller's transaction; return the imported view name."""
    model_id = str(model["semantic_model_id"])
    owner = str(model["owner_name"])
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
        # Placeholder scope name for an unsupported review draft, not a table read.
        database = "NOVA_SYSTEM"
    schema_name = str(model.get("schema_name") or "")
    if len(schema_name) > 128:
        raise ValueError("schema scope is too long")
    verified = _verified_queries(model_id, definition, legacy_verified, supported=supported)
    if contains_credential_shape(json.dumps(verified, default=str)):
        raise ValueError("legacy verified query contains a credential-shaped value")

    existing = await repo.get_view(model_id)
    if existing is not None:
        if existing["active_version"] is not None or existing["status"] != "DRAFT":
            report["already_present"] += 1
            return None
        current = await repo.get_version(model_id, 1)
        if current is not None:
            validation = _value(current.get("validation")) or {}
            if validation.get("migration", {}).get("source") != _SOURCE:
                report["unresolved"].append(
                    {"model_id": model_id, "reason": "existing view ID is not a legacy import"}
                )
                return None
            if validation.get("valid") and current["status"] == "VALIDATED":
                await repo.publish(
                    model_id, 1, expected_revision=int(existing["metadata_revision"]),
                    expected_active=None,
                )
            else:
                report["drafts_needing_review"].append(model_id)
            report["already_present"] += 1
            return None
        catalog = str(existing["catalog_name"])
        database = str(existing["database_name"])
        schema_name = str(existing["schema_name"])
        name = str(existing["name"])
    else:
        catalog = "default_catalog"
        original_name = str(model.get("name") or definition.get("name") or "")
        name = await _resolve_name(
            repo, catalog, database, schema_name, _view_name(original_name, model_id), model_id,
        )

    if supported:
        definition["name"] = name
        if model.get("description") is not None:
            definition["description"] = str(model["description"])
    definition["verified_queries"] = verified
    errors, warnings, fingerprint = _validate(
        definition, verified, supported=supported, legacy_version=legacy_version,
    )
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
            "source": _SOURCE,
            "legacy_id": model_id,
            "legacy_name": str(model.get("name") or ""),
            "legacy_owner": owner,
            "legacy_created_at": _time(model.get("created_at")),
            "legacy_updated_at": _time(model.get("updated_at")),
            "legacy_version": legacy_version,
            "source_file_id": model.get("source_file_id"),
            "verified_query_count": len(verified),
            "legacy_verified_queries": verified if not supported else [],
            "raw_definition": raw_definition if not supported else None,
            "raw_definition_preserved": not supported,
            "source_checks_executed": False,
        },
    }
    if existing is None:
        view = await repo.create_view(
            catalog=catalog, database=database, schema=schema_name, name=name,
            view_id=model_id, definition=definition, fingerprint=fingerprint,
            visibility="PRIVATE",
        )
        revision = int(view["metadata_revision"])
    else:
        await repo.add_version(model_id, definition, fingerprint, expected_latest=0)
        revision = int(existing["metadata_revision"])
    await repo.record_validation(model_id, 1, report_data)
    if not errors:
        await repo.publish(model_id, 1, expected_revision=revision, expected_active=None)
    else:
        report["drafts_needing_review"].append(model_id)
    return name


async def migrate_legacy_semantic_models() -> dict[str, Any]:
    """Idempotently import legacy metadata; return IDs needing human review."""
    actor = service_actor(MIGRATION_SERVICE)
    async with intelligence_metadata(actor, read_only=True) as repo:
        models = await repo.legacy_semantic_models()
        verified_rows = await repo.legacy_verified_queries()

    verified_by_model: dict[tuple[str, str], list[dict]] = defaultdict(list)
    invalid_verified_models: set[tuple[str, str]] = set()
    for record in verified_rows:
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
        # Each model is one transaction: the existence check, name resolution and
        # writes commit together, so a rerun sees either nothing or the whole import.
        attempt = {**report, "drafts_needing_review": [], "unresolved": []}
        try:
            if (model_id, owner) in invalid_verified_models:
                raise ValueError("a legacy verified query cannot be decoded")
            async with intelligence_metadata(actor) as repo:
                name = await _import_model(
                    repo, model, list(verified_by_model[(model_id, owner)]), attempt,
                )
        except (TypeError, ValueError, KeyError) as exc:
            report["unresolved"].append({"model_id": model_id, "reason": str(exc)})
            continue
        except (MetadataConflict, MetadataIntegrityViolation):
            report["unresolved"].append(
                {"model_id": model_id, "reason": "the Semantic View changed during import"}
            )
            continue
        report["already_present"] = attempt["already_present"]
        report["drafts_needing_review"].extend(attempt["drafts_needing_review"])
        report["unresolved"].extend(attempt["unresolved"])
        if name is None:
            continue
        await write_audit_log(
            event_type="SEMANTIC_VIEW",
            user_name=actor.principal,
            action="MIGRATE",
            object_type="SEMANTIC_VIEW",
            object_name=name,
            status="SUCCESS",
        )
        report["imported"] += 1

    for model_id, _owner in (verified_by_model.keys() | invalid_verified_models) - seen_models:
        report["unresolved"].append(
            {"model_id": model_id, "reason": "verified queries have no legacy model"}
        )
    return report
