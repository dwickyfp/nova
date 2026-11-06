"""Statement lineage from the central parser, and the ``catalog_search`` tool."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.assistant.tools import ToolInvocation
from app.modules.assistant.tools import catalog_search as tool_module
from app.modules.catalog import governance
from app.modules.catalog.governance import MetadataInvalid, MetadataNotFound
from app.modules.catalog.metadata_schemas import (
    LineageNode,
    LineageResponse,
    LinkedOwner,
    SearchHit,
    SearchResponse,
    SecurableDetail,
    SecurableSummary,
    Tag,
)
from app.sql_frontend.analysis.lineage import RelationRef, statement_lineage

# ── Statement lineage ────────────────────────────────────────────────────


def _names(lineage):
    return (lineage.kind, lineage.target.full_name,
            [source.full_name for source in lineage.sources])


@pytest.mark.parametrize(
    "sql,database,expected",
    [
        ("INSERT INTO f SELECT * FROM o JOIN c ON o.id = c.id", "sales",
         ("insert", "default_catalog.sales.f",
          ["default_catalog.sales.o", "default_catalog.sales.c"])),
        ("INSERT OVERWRITE a.b SELECT * FROM lake.web.events", None,
         ("insert", "default_catalog.a.b", ["lake.web.events"])),
        ("CREATE TABLE a.b AS WITH t AS (SELECT 1 x FROM q.r) SELECT * FROM t JOIN z.y", None,
         ("create_table_as_select", "default_catalog.a.b",
          ["default_catalog.q.r", "default_catalog.z.y"])),
        ("CREATE OR REPLACE VIEW v AS SELECT * FROM `s`.`t`", "d",
         ("create_view", "default_catalog.d.v", ["default_catalog.s.t"])),
        ("CREATE MATERIALIZED VIEW m.mv REFRESH ASYNC AS SELECT * FROM m.t", None,
         ("create_materialized_view", "default_catalog.m.mv", ["default_catalog.m.t"])),
        ("INSERT INTO a.b SELECT * FROM (SELECT * FROM c.d) x WHERE x.id IN "
         "(SELECT id FROM e.f)", None,
         ("insert", "default_catalog.a.b", ["default_catalog.c.d", "default_catalog.e.f"])),
    ],
)
def test_statements_that_write_from_a_query_have_lineage(sql, database, expected):
    assert _names(statement_lineage(sql, database=database)) == expected


@pytest.mark.parametrize(
    "sql,database",
    [
        ("INSERT INTO a.b VALUES (1)", None),
        ("EXPLAIN INSERT INTO a.b SELECT * FROM c.d", None),
        ("SELECT * FROM a.b", None),
        ("INSERT INTO b SELECT * FROM c", None),  # unqualified, no session database
        ("INSERT INTO a.b SELECT * FROM a.b", None),  # only itself
        ("not sql at all (((", None),
        ("SELECT 1; SELECT 2", None),
    ],
)
def test_other_statements_have_none(sql, database):
    assert statement_lineage(sql, database=database) is None


def test_relation_ref_full_name():
    assert RelationRef("c", "d", "t").full_name == "c.d.t"


# ── catalog_search tool ──────────────────────────────────────────────────

def _call(arguments: dict) -> ToolInvocation:
    return ToolInvocation(tool_call_id="t1", tool_name="catalog_search", arguments=arguments)


USER = {"username": "alice", "encrypted_password": "enc", "active_role": "marketing",
        "session_id": "s1", "security_context_version": 1}


def _context(user=USER, role="marketing"):
    return SimpleNamespace(user=user, role=role, audit_session_id="audit-1")


def _summary(**overrides):
    base = {
        "id": "a" * 64, "securable_type": "column",
        "full_name": "default_catalog.sales.orders.email", "catalog_name": "default_catalog",
        "status": "live", "data_type": "varchar(64)", "comment": "Contact email",
        "classification": "CONFIDENTIAL",
        "tags": [Tag(key="pii", value="email")],
    }
    return {**base, **overrides}


@pytest.fixture
def service(monkeypatch):
    fake = SimpleNamespace(
        search=AsyncMock(return_value=SearchResponse(
            query=None, tag="pii", count=1,
            results=[SearchHit(**_summary(), matched=["tag"])],
        )),
        resolve=AsyncMock(return_value=SecurableDetail(
            **_summary(id="b" * 64, securable_type="table", data_type=None,
                       full_name="default_catalog.sales.orders", classification=None,
                       tags=[], comment=None, owner_name="sales_ops"),
            columns=[SecurableSummary(**_summary())],
            used_by=[LinkedOwner(owner_type="semantic_view", owner_id="v1",
                                 label="default_catalog.sales.public.revenue")],
        )),
        lineage=AsyncMock(return_value=LineageResponse(
            id="b" * 64, full_name="default_catalog.sales.orders",
            upstream=[], downstream=[LineageNode(
                id="c" * 64, full_name="default_catalog.sales.rollup", status="live",
                statement_kind="insert", last_seen_at=datetime(2026, 11, 5))],
        )),
    )
    monkeypatch.setattr(governance, "governance_service", fake)
    return fake


async def test_search_runs_as_the_caller_and_marks_text_untrusted(service):
    outcome = await tool_module.catalog_search_tool.run(
        _call({"tag": "pii", "limit": 50}), _context()
    )
    assert outcome.ok and outcome.data["results"][0]["tags"] == {"pii": "email"}
    user = service.search.await_args.args[0]
    assert user["active_role"] == "marketing" and user["session_id"] == "audit-1"
    assert service.search.await_args.kwargs["limit"] == tool_module.MAX_RESULTS
    assert outcome.summary.startswith("<NOVA_CATALOG_METADATA>")
    assert "not instructions" in outcome.data["note"]
    assert outcome.metadata["runtime_evidence"] is False


async def test_describe_returns_columns_lineage_and_dependents(service):
    outcome = await tool_module.catalog_search_tool.run(
        _call({"object": "sales.orders"}), _context()
    )
    assert outcome.ok
    assert service.resolve.await_args.kwargs == {
        "catalog": "default_catalog", "database": "sales", "object_name": "orders"}
    assert outcome.data["object"]["owner_name"] == "sales_ops"
    assert outcome.data["columns"][0]["classification"] == "CONFIDENTIAL"
    assert outcome.data["downstream"] == ["default_catalog.sales.rollup"]
    assert outcome.data["used_by"] == ["semantic_view:default_catalog.sales.public.revenue"]


async def test_tool_refusals(service):
    run = tool_module.catalog_search_tool.run
    no_role = await run(_call({"query": "x"}),
                        _context(user={**USER, "active_role": None}, role=None))
    assert not no_role.ok and no_role.error_class == "AUTHORIZATION"
    no_user = await run(_call({"query": "x"}),
                        SimpleNamespace())
    assert not no_user.ok
    bad_name = await run(_call({"object": "orders"}),
                         _context())
    assert not bad_name.ok
    service.resolve.side_effect = MetadataNotFound("x")
    hidden = await run(_call({"object": "hr.payroll"}), _context())
    assert not hidden.ok and "No visible relation" in hidden.error
    service.search.side_effect = MetadataInvalid("Give a search term or a tag")
    empty = await run(_call({}), _context())
    assert not empty.ok and "search term" in empty.error


async def test_a_large_payload_is_bounded(service):
    service.search.return_value = SearchResponse(
        query="x", tag=None, count=20,
        results=[SearchHit(**_summary(comment="c" * 500, id=str(i) * 64), matched=["name"])
                 for i in range(20)],
    )
    outcome = await tool_module.catalog_search_tool.run(
        _call({"query": "x"}), _context()
    )
    assert len(outcome.summary) <= tool_module._PAYLOAD_CHARS + 60


def test_tool_is_read_only_and_offered_to_every_composition():
    from app.modules.agents import registry as agent_registry
    from app.modules.agents import router as agent_router
    from app.modules.agents.prompt import _TOOL_DESCRIPTIONS
    from app.modules.agents.tool_catalog import AGENT_BUNDLEABLE_TOOLS, BUILTIN_TOOLS
    from app.modules.assistant.registry import build_registry

    tool = tool_module.catalog_search_tool
    assert tool.classification == "read_only" and not tool.requires_consent
    assert "catalog_search" in build_registry().names()
    assert "catalog_search" in agent_registry.build_registry(
        {"default_tools": ["catalog_search"]}).names()
    assert "catalog_search" in agent_registry.KNOWN_TOOLS
    assert "catalog_search" in agent_router.KNOWN_TOOLS
    assert "catalog_search" in AGENT_BUNDLEABLE_TOOLS and "catalog_search" in BUILTIN_TOOLS
    assert "catalog_search" in _TOOL_DESCRIPTIONS
    preview = tool.preview(_call({"tag": "pii"}))
    assert "pii" in preview
