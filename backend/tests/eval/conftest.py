"""Eval-suite configuration."""

import pytest


@pytest.fixture(autouse=True)
def _no_published_ontology(monkeypatch):
    """No metadata database here: governed queries see no published ontology.

    Ontology tests build their own ``ConceptResolver`` and are unaffected.
    """
    from app.modules.ontology.grounding import concept_resolver
    from app.modules.ontology.resolver import Resolution

    async def no_release(*_args, **_kwargs):
        return Resolution(status="no_release")

    monkeypatch.setattr(concept_resolver, "resolve", no_release)
