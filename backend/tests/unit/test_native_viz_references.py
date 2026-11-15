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
