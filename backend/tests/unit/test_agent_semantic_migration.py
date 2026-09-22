"""Additive persistence contract for agent and semantic intelligence."""

from pathlib import Path

from app.modules.agents.repository import (
    AGENTS_DDL,
    SEMANTIC_USAGE_DDL,
    VERIFIED_QUERIES_DDL,
)


def test_runtime_schema_contains_backward_compatible_agent_columns():
    assert "discoverable_skills" in AGENTS_DDL
    assert "compiled_instructions" in AGENTS_DDL
    assert "harness_mode" in AGENTS_DDL


def test_runtime_schema_contains_versioned_vqr_and_usage_tables():
    assert "model_fingerprint" in VERIFIED_QUERIES_DDL
    assert "semantic_plan JSON" in VERIFIED_QUERIES_DDL
    assert "verified_sql TEXT" in VERIFIED_QUERIES_DDL
    assert "AUDIT_SEMANTIC_QUERY_USAGE" in SEMANTIC_USAGE_DDL


def test_additive_migration_preserves_legacy_agent_fields():
    root = Path(__file__).resolve().parents[2]
    sql = (root / "migrations" / "20260921_agent_semantic_intelligence.sql").read_text()
    assert "ALTER TABLE NOVA_SYSTEM.CONFIG_AGENTS ADD COLUMN discoverable_skills" in sql
    assert "DROP COLUMN" not in sql.upper()
    assert "CONFIG_SEMANTIC_VERIFIED_QUERIES" in sql
