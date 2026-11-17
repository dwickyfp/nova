"""Graph traversal bounds complement the real-PG reference authorization tests."""

from collections import Counter
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.exceptions import NovaException
from app.modules.viz import documents
from app.modules.viz.contracts.dashboards import DashboardDefinition
from app.modules.viz.documents import VizDocumentRepository, canonical
from tests.unit.test_native_viz_bindings import chart, dataset


def related(identity, children=()):
    return dataset(dataset_id=identity, relationships=[{
        "relationship_id": uuid4(), "dataset_id": child, "dataset_revision": revision,
        "keys": [{"local_field_id": "region", "foreign_field_id": "region"}],
        "cardinality": "many:1", "join": "left",
    } for child, revision in children])


class Graph:
    def __init__(self, entries):
        self.entries = entries
        self.reads = Counter()

    async def _head(self, identity):
        return {"workspace_id": "workspace", "kind": "dataset"}

    async def _definition(self, identity, revision, kind):
        self.reads[(identity, revision)] += 1
        return self.entries[(identity, revision)]

    async def references(self, root):
        return await VizDocumentRepository._references(self, root, "workspace")


async def test_dashboard_data_registry_retains_sources_without_chart_widgets():
    source, related_id = uuid4(), uuid4()
    graph = Graph({(source, 3): related(source, [(related_id, 2)]),
                   (related_id, 2): related(related_id)})
    board = DashboardDefinition.model_validate({
        "dashboard_id": uuid4(), "title": "Data first",
        "pages": [{"page_id": uuid4(), "title": "Overview"}],
        "datasets": [{"dataset_id": source, "revision": 3}],
    })
    assert set(await graph.references(board)) == {(source, 3), (related_id, 2)}


async def test_local_metric_source_pins_and_authorizes_its_base_dataset():
    base = dataset()
    local = dataset(source={"kind": "dashboard_local_metric_view",
                            "dataset_id": base.dataset_id, "dataset_revision": 7})
    graph = Graph({(base.dataset_id, 7): base})
    assert await graph.references(local) == [(base.dataset_id, 7)]
    assert graph.reads[(base.dataset_id, 7)] == 1


async def test_local_metric_source_cycle_is_refused():
    first, second = uuid4(), uuid4()
    root = dataset(dataset_id=first, source={"kind": "dashboard_local_metric_view",
                                            "dataset_id": second, "dataset_revision": 1})
    child = dataset(dataset_id=second, source={"kind": "dashboard_local_metric_view",
                                             "dataset_id": first, "dataset_revision": 1})
    graph = Graph({(first, 1): root, (second, 1): child})
    with pytest.raises(ValueError, match="cyclic"):
        await graph.references(root)


async def test_shared_dataset_dag_is_validated_once_per_edge_not_per_path(monkeypatch):
    layers = [[uuid4(), uuid4()] for _ in range(12)]
    entries = {}
    for index, layer in enumerate(layers):
        children = [(identity, 1) for identity in layers[index + 1]] if index < 11 else []
        for identity in layer:
            entries[(identity, 1)] = related(identity, children)
    root = related(uuid4(), [(identity, 1) for identity in layers[0]])
    graph = Graph(entries)
    validate = documents.validate_relationship_binding
    validations = []

    def counted(*args):
        validations.append(args[0].dataset_id)
        return validate(*args)

    monkeypatch.setattr(documents, "validate_relationship_binding", counted)
    references = await graph.references(root)
    assert set(references) == set(entries)
    assert len(validations) == 46
    assert set(graph.reads.values()) == {1}


async def test_cached_subtree_cannot_hide_cycle_across_dataset_revisions():
    first, second = uuid4(), uuid4()
    entries = {
        (first, 1): related(first, [(second, 1)]),
        (second, 1): related(second),
        (second, 2): related(second, [(first, 1)]),
    }
    root = related(uuid4(), [(first, 1), (second, 2)])
    with pytest.raises(ValueError, match="cyclic or too deep"):
        await Graph(entries).references(root)


async def test_cached_subtree_depth_is_checked_under_each_parent():
    chain = [uuid4() for _ in range(16)]
    entries = {
        (identity, 1): related(identity, [(chain[index + 1], 1)] if index < 15 else [])
        for index, identity in enumerate(chain)
    }
    root = related(uuid4(), [(chain[8], 1), (chain[0], 1)])
    with pytest.raises(ValueError, match="cyclic or too deep"):
        await Graph(entries).references(root)


async def test_complete_sixteen_level_graph_is_valid():
    chain = [uuid4() for _ in range(16)]
    entries = {
        (identity, 1): related(identity, [(chain[index + 1], 1)] if index < 15 else [])
        for index, identity in enumerate(chain)
    }
    root = entries.pop((chain[0], 1))
    assert set(await Graph(entries).references(root)) == set(entries)


async def test_digest_verifies_stored_document_before_applying_new_optional_defaults():
    saved = chart(dataset())
    payload = saved.model_dump(mode="json")
    del payload["encoding"]["dimensions"][0]["discrete"]
    row = {"definition": payload, "digest": sha256(canonical(payload)).hexdigest()}

    class Connection:
        async def execute(self, *_args):
            return self

        async def fetchone(self):
            return row

    repository = SimpleNamespace(connection=Connection())
    repository._revision_payload = VizDocumentRepository._revision_payload.__get__(repository)
    restored = await VizDocumentRepository._definition(repository, saved.chart_id, 1, "chart")
    assert restored.encoding.dimensions[0].discrete is True
    payload["title"] = "Tampered title"
    with pytest.raises(NovaException, match="integrity"):
        await VizDocumentRepository._definition(repository, saved.chart_id, 1, "chart")


# ── NDV-METRIC-018: a dashboard-local metric and a shared one share a name ──


def _semantic_registry_dataset(model_revision: int):
    """A dataset over the shared semantic registry: its metric is addressed by its
    model field id and pinned by the model revision and fingerprint."""
    return dataset(
        source={"kind": "semantic_model", "model_id": "sales_model",
                "model_revision": model_revision, "model_fingerprint": "b" * 64,
                "metrics": ["revenue"], "database": "sales",
                "dependencies": [{"kind": "table", "catalog": "default_catalog",
                                  "database": "sales", "object_name": "orders",
                                  "physical_id": "test-table-id"}]},
        fields=[{"field_id": "region", "name": "region", "source_column": "region",
                 "logical_type": "string", "physical_type": "VARCHAR(30)", "nullable": True,
                 "semantic_role": "dimension"},
                {"field_id": "revenue", "name": "Revenue", "source_column": "revenue",
                 "logical_type": "decimal", "physical_type": "DECIMAL(38,2)", "nullable": True,
                 "semantic_role": "measure"}],
    )


async def test_a_local_and_a_shared_metric_with_one_name_resolve_to_distinct_pinned_ids():
    from app.modules.viz.bindings import validate_chart_binding

    shared = _semantic_registry_dataset(model_revision=4)
    local_metric = uuid4()
    local = dataset(
        source={"kind": "dashboard_local_metric_view", "dataset_id": shared.dataset_id,
                "dataset_revision": 3},
        fields=[field.model_dump() for field in shared.fields],
        metrics=[{"metric_id": local_metric, "revision": 2, "name": "Revenue",
                  "formula": "SUM([revenue]) * 1.1", "level": "aggregate", "grain": [],
                  "unit": "currency", "scope": "dashboard_local"}],
    )
    # One name, two identities: the shared metric is the registry field id, the
    # local one its own metric id; neither is found by the display name.
    on_shared = chart(shared, dataset_revision=3, encoding={
        "kind": "cartesian", "dimensions": [{"field_id": "region"}],
        "measures": [{"field_id": "revenue", "aggregation": "sum"}]})
    on_local = chart(local, dataset_revision=1, encoding={
        "kind": "cartesian", "dimensions": [{"field_id": "region"}],
        "measures": [{"field_id": str(local_metric), "aggregation": "agg"}]})
    validate_chart_binding(on_shared, shared)
    validate_chart_binding(on_local, local)
    by_name = on_local.model_dump()
    by_name["encoding"]["measures"][0]["field_id"] = "Revenue"
    with pytest.raises(ValueError, match="absent"):
        validate_chart_binding(chart(local).model_validate(by_name), local)
    # The local metric pins the shared dataset revision it was built on: a newer
    # registry model (revision 5, a new dataset revision 4) changes neither the
    # local metric's id and revision nor which shared revision it reads.
    newer = _semantic_registry_dataset(model_revision=5)
    graph = Graph({(shared.dataset_id, 3): shared, (shared.dataset_id, 4): newer})
    assert await graph.references(local) == [(shared.dataset_id, 3)]
    assert graph.reads == Counter({(shared.dataset_id, 3): 1})
    assert (local.metrics[0].metric_id, local.metrics[0].revision) == (local_metric, 2)
    assert local.metrics[0].scope == "dashboard_local"
    assert shared.source.model_revision == 4 and newer.source.model_revision == 5
