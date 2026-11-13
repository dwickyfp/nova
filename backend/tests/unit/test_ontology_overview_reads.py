"""The Overview reads each kind of ontology metadata once per request."""

from __future__ import annotations

import pytest

from app.modules.ontology import lint as lint_module
from app.modules.ontology.contracts import Domain
from app.modules.ontology.evaluation.metrics import _OnceRepository


class CountingRepository:
    def __init__(self) -> None:
        self.reads: list[str] = []

    async def current(self, kind, model):
        self.reads.append(kind)
        return []

    def table_name(self) -> str:
        return "delegated"


async def test_each_kind_is_read_once_and_callers_get_their_own_list():
    inner = CountingRepository()
    repository = _OnceRepository(inner)
    first = await repository.current("domains", Domain)
    first.append("mutated by a caller")
    assert await repository.current("domains", Domain) == []
    await repository.current("concepts", Domain)
    assert inner.reads == ["domains", "concepts"]
    assert repository.table_name() == "delegated"


async def test_lint_reuses_a_layer_it_is_given(monkeypatch):
    async def must_not_run(*args, **kwargs):
        pytest.fail("visible_layer was computed again")

    monkeypatch.setattr(lint_module, "visible_layer", must_not_run)
    findings = await lint_module.lint(
        {"username": "alice"}, object(), _OnceRepository(CountingRepository()),
        layer={"items": []},
    )
    assert findings == []
