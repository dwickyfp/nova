"""Data answers name their sources the way a reader does.

A lay reader does not know the governing layer ("Semantic View") or a view's
internal name (``retail_sales``). The views that answered are named by their
label instead, and nothing else in the answer changes.
"""

from __future__ import annotations

import pytest

from app.modules.assistant.plain_language import (
    find_technical_leaks,
    plain_data_sources,
    source_label,
)
from app.modules.assistant.router import NEVA_GROUNDING

VIEWS = ["retail_sales", "outlet_stock"]


@pytest.mark.parametrize(("name", "label"), [
    ("retail_sales", "Retail Sales"),
    ("nova_business_360", "Nova Business 360"),
    ("outlet-stock", "Outlet Stock"),
    ("KPI_daily", "KPI Daily"),
])
def test_a_view_name_becomes_its_label(name, label):
    assert source_label(name) == label


@pytest.mark.parametrize(("text", "language", "expected"), [
    ("berdasarkan Semantic View **retail_sales**, untuk", "id",
     "berdasarkan data **Retail Sales**, untuk"),
    ("pada tampilan semantik **outlet_stock**.", "id", "pada data **Outlet Stock**."),
    ("berdasarkan tampilan **retail_sales**.", "id", "berdasarkan data **Retail Sales**."),
    ("all data in the **retail_sales** Semantic View.", "en",
     "all data in the **Retail Sales** data."),
    ("using the published **retail_sales** view", "en",
     "using the published **Retail Sales** data"),
    ("The `retail_sales` Semantic View returned", "en", "The Retail Sales data returned"),
    ("The Retail Sales Semantic View returned", "en", "The Retail Sales data returned"),
    ("the approved definition in **retail_sales**, with", "en",
     "the approved definition in **Retail Sales**, with"),
])
def test_sources_are_named_by_their_label(text, language, expected):
    assert plain_data_sources(text, VIEWS, language) == expected


def test_only_the_views_that_answered_are_renamed():
    text = "From **retail_sales**; **other_view** was not used."
    assert plain_data_sources(text, ["retail_sales"], "en") == (
        "From **Retail Sales**; **other_view** was not used."
    )


def test_a_plain_word_view_name_leaves_ordinary_prose_alone():
    text = "Total sales grew, per the **sales** view and **sales**."
    assert plain_data_sources(text, ["sales"], "en") == (
        "Total sales grew, per the **Sales** data and **Sales**."
    )


def test_code_the_reader_asked_for_keeps_its_names():
    text = "```sql\nSELECT * FROM retail_sales\n```\nFrom retail_sales."
    assert plain_data_sources(text, VIEWS, "en") == (
        "```sql\nSELECT * FROM retail_sales\n```\nFrom Retail Sales."
    )


def test_no_answering_view_changes_nothing():
    text = "Open AI & ML → Semantic Views to create a Semantic View."
    assert plain_data_sources(text, [], "en") == text


def test_neva_is_told_to_name_sources_plainly():
    assert "Never write a view's internal name, the words Semantic View" in NEVA_GROUNDING
    assert "SQL the person asked to see or edit keeps its own words" in NEVA_GROUNDING
    assert not find_technical_leaks(plain_data_sources("Per **retail_sales**.", VIEWS, "en"))


def test_the_loop_names_the_views_its_queries_read():
    from app.modules.assistant.intelligence import EvidenceTracker
    from app.modules.assistant.service import _named_sources_plainly

    evidence = EvidenceTracker()
    evidence.add("semantic_query", "rows", metadata={"view_name": "retail_sales"})
    evidence.add("query_execute", "rows", metadata={"view_name": "outlet_stock"})
    answer = "Per Semantic View **retail_sales** and **outlet_stock**."
    assert _named_sources_plainly(answer, evidence, "en") == (
        "Per **Retail Sales** data and **outlet_stock**."
    )
