"""Schema-3 dashboards: the free canvas contract, the v2 upcaster and canvas operations.

Golden files in ``tests/fixtures/viz_dashboards_v2`` pin the upcast of every v2
fixture; ``legacy-operations.json`` pins receipt digests computed with the
schema-2 code before this change, so stored receipts still replay.
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.modules.viz.canvas import (
    LEGACY_GRID,
    cell_of,
    cell_rect,
    dashboard_definition,
    legacy_widgets,
    reading_order,
    tab_order,
)
from app.modules.viz.contracts.commands import Operation
from app.modules.viz.contracts.dashboards import (
    MAX_DOCUMENT_BYTES,
    DashboardDefinition,
    Page,
)
from app.modules.viz.contracts.dashboards_v2 import DashboardDefinitionV2, Layout
from app.modules.viz.documents import canonical, parse_document
from app.modules.viz.operations import apply_operation

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "viz_dashboards_v2"
GOLDEN = sorted(path for path in FIXTURES.glob("*.json")
                if not path.name.endswith(".v3.json") and path.stem != "legacy-operations")
#: sha256 of each legacy operation's receipt intent, computed with the schema-2
#: code at origin/main 305d3f13 (resource 00000000-0000-4000-8000-000000000030).
LEGACY_DIGESTS = [
    "9b1e5849c4f749a1f384ee1e14968c6c6e82c2921236bbcf0eee4c5cf580f703",
    "989589ed8febccf661ef29e4d9f4c9bb0708b5b5ec73b2ba9ee79bc3100b97e6",
    "b8326bf56afd95210a42c5c58c0785aa27587e410d71a8f78f8a1473d7370b3e",
]


def load(path: Path) -> Any:
    return json.loads(path.read_text())


def ident() -> str:
    return str(uuid4())


# ── Upcaster: golden, deterministic, lossless ───────────────────────────

def test_there_are_golden_fixtures():
    assert len(GOLDEN) >= 5


@pytest.mark.parametrize("path", GOLDEN, ids=lambda path: path.stem)
def test_every_v2_fixture_upcasts_to_its_golden_v3(path):
    upcast = dashboard_definition(load(path))
    assert upcast.model_dump(mode="json") == load(path.with_suffix(".v3.json"))
    # Golden v3 parses as itself: a stored v3 revision reads back unchanged.
    assert dashboard_definition(load(path.with_suffix(".v3.json"))) == upcast


@pytest.mark.parametrize("path", GOLDEN, ids=lambda path: path.stem)
def test_upcast_is_lossless_and_reversible(path):
    original = DashboardDefinitionV2.model_validate(load(path))
    upcast = dashboard_definition(load(path))
    assert canonical(upcast) == canonical(dashboard_definition(json.loads(json.dumps(load(path)))))
    for before, after in zip(original.pages, upcast.pages, strict=True):
        assert after.page_id == before.page_id and after.title == before.title
        assert [w.model_dump() for w in legacy_widgets(after)] == [
            w.model_dump() for w in [*[w for w in before.widgets if w.kind != "spacer"],
                                     *[w for w in before.widgets if w.kind == "spacer"]]]
    for name in ("dashboard_id", "title", "description", "datasets", "filters", "actions"):
        assert getattr(upcast, name) == getattr(original, name)


def test_every_legacy_cell_maps_to_whole_pixels_and_back():
    for x in range(12):
        for w in range(2, 13 - x):
            for y, h in ((0, 2), (3, 48), (10_000, 2), (77, 5)):
                layout = Layout(x=x, y=y, w=w, h=h)
                rect = cell_rect(layout, LEGACY_GRID, 1200)
                assert (rect.x, rect.y, rect.w, rect.h) == (
                    x * 100 + 6, y * 72 + 6, w * 100 - 12, h * 72 - 12)
                assert cell_of(rect, LEGACY_GRID, 1200) == layout


def _random_v2(seed: int) -> dict[str, Any]:
    """A valid, non-overlapping v2 dashboard built from a seed."""
    rng = random.Random(seed)
    pages = []
    for _ in range(rng.randint(1, 3)):
        widgets, occupied = [], []
        for _ in range(rng.randint(0, 14)):
            w, h = rng.randint(2, 12), rng.randint(2, 6)
            x, y = rng.randint(0, 12 - w), rng.randint(0, 30)
            if any(x < ox + ow and ox < x + w and y < oy + oh and oy < y + h
                   for ox, oy, ow, oh in occupied):
                continue
            occupied.append((x, y, w, h))
            base = {"widget_id": ident(), "layout": {"x": x, "y": y, "w": w, "h": h},
                    "title": rng.choice(["", "Revenue", "Orders"]), "locked": rng.random() < .2,
                    "appearance": {"surface": rng.choice(["card", "transparent"]),
                                   "border": rng.random() < .5,
                                   "padding": rng.choice(["none", "compact", "comfortable"])}}
            kind = rng.choice(["chart", "text", "image", "spacer"])
            if kind == "chart":
                base.update(kind="chart", chart_ref={"chart_id": ident(), "revision": 1},
                            style={"show_legend": rng.choice([None, True, False])})
            elif kind == "text":
                base.update(kind="text", text=rng.choice(["", "Hello", "Line\nbreak"]),
                            style=rng.choice(["title", "body"]))
            elif kind == "image":
                base.update(kind="image", asset_id=ident(), alt="Logo",
                            fit=rng.choice(["contain", "cover"]))
            else:
                base.update(kind="spacer")
            widgets.append(base)
        pages.append({"page_id": ident(), "title": "Page", "widgets": widgets})
    return {"dashboard_id": ident(), "title": "Random", "pages": pages}


@pytest.mark.parametrize("seed", range(300))
def test_property_upcast_round_trips_any_valid_v2_dashboard(seed):
    payload = _random_v2(seed)
    original = DashboardDefinitionV2.model_validate(payload)
    upcast = dashboard_definition(payload)
    assert upcast == dashboard_definition(payload)  # deterministic
    for before, after in zip(original.pages, upcast.pages, strict=True):
        expected = [w for w in before.widgets if w.kind != "spacer"]
        expected += [w for w in before.widgets if w.kind == "spacer"]
        assert legacy_widgets(after) == expected
        bottom = max((w.layout.y + w.layout.h for w in before.widgets), default=0)
        assert after.canvas.height == max(720, bottom * 72)
        assert after.canvas.width == 1200 and after.canvas.fit == "width"


def test_stored_v2_revisions_keep_their_digest_and_are_upcast_on_read():
    for path in GOLDEN:
        raw = load(path)
        digest = hashlib.sha256(canonical(raw)).hexdigest()
        document = parse_document("dashboard", raw)
        assert isinstance(document, DashboardDefinition) and document.schema_version == 3
        assert hashlib.sha256(canonical(raw)).hexdigest() == digest  # never rewritten


def test_legacy_operation_receipt_digests_replay_unchanged():
    for payload, expected in zip(load(FIXTURES / "legacy-operations.json"), LEGACY_DIGESTS,
                                 strict=True):
        operation = Operation.model_validate(payload)
        intent = {"command": "apply", "resource_id": "00000000-0000-4000-8000-000000000030",
                  "operation": operation.model_dump(mode="json")}
        assert hashlib.sha256(canonical(intent)).hexdigest() == expected


# ── v3 contract ─────────────────────────────────────────────────────────

def visual(element_id: str | None = None, **geometry: int) -> dict[str, Any]:
    return {"kind": "visual", "element_id": element_id or ident(),
            "geometry": {"x": 0, "y": 0, "w": 400, "h": 300, **geometry},
            "chart_ref": {"chart_id": ident(), "revision": 1}}


def page(*elements: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"page_id": ident(), "title": "Canvas", "elements": list(elements), **extra}


def v3(*pages: dict[str, Any], **extra: Any) -> DashboardDefinition:
    return DashboardDefinition.model_validate({
        "dashboard_id": ident(), "title": "Canvas", "pages": list(pages) or [page()], **extra})


def test_new_pages_default_to_a_16_9_frame_with_a_snapping_grid():
    blank = Page(page_id=uuid4(), title="New")
    assert (blank.canvas.size, blank.canvas.width, blank.canvas.height) == ("16:9", 1280, 720)
    grid = blank.canvas.grid
    assert (grid.columns, grid.gutter, grid.baseline, grid.show, grid.snap,
            grid.snap_to_elements) == (12, 16, 8, True, True, True)


@pytest.mark.parametrize("canvas", [
    {"size": "16:9", "width": 1200, "height": 720},
    {"size": "custom", "width": 319, "height": 720},
    {"size": "custom", "width": 400, "height": 720, "grid": {"columns": 24, "gutter": 16}},
])
def test_page_sizes_and_grids_must_fit(canvas):
    with pytest.raises(ValidationError):
        v3(page(canvas=canvas))


def test_elements_may_overlap_and_overhang_slightly_but_not_leave_the_page():
    v3(page(visual(), visual(x=100, y=100), visual(x=-64, y=-64)))
    for geometry in ({"x": -65}, {"x": 1280 - 400 + 65}, {"y": 720 - 300 + 65}):
        with pytest.raises(ValidationError, match="-64|overhang"):
            v3(page(visual(**geometry)))


def test_a_page_holds_at_most_150_elements_including_grouped_ones():
    many = [visual(x=index % 800, y=index % 400) for index in range(150)]
    v3(page(*many))
    with pytest.raises(ValidationError):
        v3(page(*many, visual()))
    group = {"kind": "group", "element_id": ident(), "geometry": {"x": 0, "y": 0, "w": 800,
                                                                  "h": 400},
             "children": many[:2]}
    with pytest.raises(ValidationError, match="at most 150"):
        v3(page(*many[2:], group, visual()))


def test_groups_nest_three_levels_and_keep_children_inside():
    leaf = visual(w=10, h=10)
    nested: dict[str, Any] = leaf
    for depth in range(3):
        nested = {"kind": "group", "element_id": ident(),
                  "geometry": {"x": 0, "y": 0, "w": 100, "h": 100}, "children": [nested]}
        if depth < 2:
            v3(page(nested))
    with pytest.raises(ValidationError, match="3 levels"):
        v3(page(nested))
    outside = {"kind": "group", "element_id": ident(),
               "geometry": {"x": 0, "y": 0, "w": 100, "h": 100},
               "children": [visual(x=200, w=10, h=10)]}
    with pytest.raises(ValidationError, match="inside their group"):
        v3(page(outside))


def test_connectors_annotations_and_values_refer_to_this_pages_visuals():
    chart = visual()
    note = {"kind": "text", "element_id": ident(), "geometry": {"x": 500, "y": 0, "w": 200,
                                                                "h": 40}}
    arrow = {"kind": "connector", "element_id": ident(),
             "geometry": {"x": 400, "y": 0, "w": 100, "h": 40},
             "start": {"kind": "element", "element_id": note["element_id"]},
             "end": {"kind": "datum", "anchor": {"element_id": chart["element_id"],
                                                 "rule": "max"}}}
    bound = {**note, "element_id": ident(), "text": {"runs": [
        {"kind": "text", "text": "Peak "},
        {"kind": "value", "value": {"element_id": chart["element_id"], "measure_id": "revenue",
                                    "rule": "max", "format": "currency"}}]}}
    v3(page(chart, note, arrow, bound))
    stray = {**arrow, "element_id": ident(),
             "start": {"kind": "element", "element_id": ident()}}
    with pytest.raises(ValidationError, match="attach to another element"):
        v3(page(chart, note, stray))
    on_text = {**arrow, "element_id": ident(),
               "end": {"kind": "datum", "anchor": {"element_id": note["element_id"],
                                                   "rule": "max"}}}
    with pytest.raises(ValidationError, match="visual on the same page"):
        v3(page(chart, note, on_text))


def test_a_data_anchor_needs_exactly_one_locator():
    chart = visual()
    target = {"element_id": chart["element_id"]}
    base = {"kind": "annotation", "element_id": ident(),
            "geometry": {"x": 0, "y": 0, "w": 100, "h": 40}}
    match = [{"field_id": "month", "value": {"type": "date", "value": "2026-09-01"}}]
    v3(page(chart, {**base, "target": {**target, "match": match}}))
    for anchor in (target, {**target, "match": match, "rule": "max"}):
        with pytest.raises(ValidationError, match="either dimension values or a rule"):
            v3(page(chart, {**base, "target": anchor}))


def test_tab_order_and_mobile_projection_name_this_pages_elements():
    first, second = visual(), visual(x=500)
    decorative = {"kind": "shape", "element_id": ident(), "decorative": True,
                  "geometry": {"x": 0, "y": 0, "w": 10, "h": 10}}
    ids = [first["element_id"], second["element_id"]]
    v3(page(first, second, decorative, tab_order=ids[::-1],
            mobile={"order": ids, "hidden": [decorative["element_id"]]}))
    for extra in ({"tab_order": [ids[0], ids[0]]}, {"tab_order": [decorative["element_id"]]},
                  {"mobile": {"order": [ident()]}}):
        with pytest.raises(ValidationError, match="distinct elements"):
            v3(page(first, second, decorative, **extra))


def test_icons_come_from_the_allow_list_and_colors_from_theme_tokens():
    icon = {"kind": "icon", "element_id": ident(), "icon": "trending-up", "label": "Growth",
            "geometry": {"x": 0, "y": 0, "w": 24, "h": 24},
            "style": {"fill": {"kind": "solid", "token": "brand"}}}
    v3(page(icon))
    with pytest.raises(ValidationError):
        v3(page({**icon, "icon": "<svg onload=alert(1)>"}))
    with pytest.raises(ValidationError):
        v3(page({**icon, "style": {"fill": {"kind": "solid", "token": "#ff0000"}}}))


def test_ids_are_unique_and_references_resolve_across_the_dashboard():
    chart = visual()
    with pytest.raises(ValidationError, match="unique"):
        v3(page(chart), page({**visual(), "element_id": chart["element_id"]}))
    with pytest.raises(ValidationError, match="Interaction source must be a visual"):
        v3(page(chart), actions=[{"action_id": ident(), "kind": "navigate",
                                  "source_widget_id": ident(), "target_page_id": ident()}])


def test_documents_are_bounded_to_one_mebibyte():
    text = {"kind": "text", "element_id": ident(), "geometry": {"x": 0, "y": 0, "w": 10,
                                                                "h": 10}}
    long = [{**text, "element_id": ident(), "text": {"runs": [{"kind": "text",
                                                               "text": "x" * 8000}]}}
            for _ in range(140)]
    with pytest.raises(ValidationError, match="one MiB"):
        v3(page(*long))
    assert MAX_DOCUMENT_BYTES == 1_048_576


# ── Canvas operations ───────────────────────────────────────────────────

def apply(document: DashboardDefinition, *changes: dict[str, Any]) -> DashboardDefinition:
    return apply_operation(document, Operation.model_validate({
        "operation_id": ident(), "expected_revision": 1, "changes": list(changes)}))


def shape(**geometry: int) -> dict[str, Any]:
    return {"kind": "shape", "element_id": ident(),
            "geometry": {"x": 0, "y": 0, "w": 50, "h": 50, **geometry}}


def test_elements_add_stacks_front_back_or_above():
    a, b, c, d = shape(), shape(), shape(), shape()
    document = v3(page(a))
    page_id = str(document.pages[0].page_id)
    document = apply(document,
                     {"op": "elements.add", "page_id": page_id, "elements": [b]},
                     {"op": "elements.add", "page_id": page_id, "elements": [c], "stack": "back"},
                     {"op": "elements.add", "page_id": page_id, "elements": [d],
                      "stack": {"above": a["element_id"]}})
    assert [str(e.element_id) for e in document.pages[0].elements] == [
        c["element_id"], a["element_id"], d["element_id"], b["element_id"]]
    with pytest.raises(ValueError, match="already exists"):
        apply(document, {"op": "elements.add", "page_id": page_id, "elements": [a]})


def test_elements_update_merges_and_revalidates():
    box = shape()
    document = v3(page(box))
    page_id = str(document.pages[0].page_id)
    updated = apply(document, {"op": "elements.update", "page_id": page_id, "updates": [
        {"element_id": box["element_id"], "set": {"name": "Hero band", "shape": "pill",
                                                  "style": {"radius": 16}}}]})
    element = updated.pages[0].elements[0]
    assert (element.name, element.shape, element.style.radius) == ("Hero band", "pill", 16)
    assert element.geometry == document.pages[0].elements[0].geometry
    with pytest.raises(ValidationError):  # a shape has no chart reference
        apply(document, {"op": "elements.update", "page_id": page_id, "updates": [
            {"element_id": box["element_id"],
             "set": {"chart_ref": {"chart_id": ident(), "revision": 1}}}]})
    with pytest.raises(ValidationError, match="at least one"):
        apply(document, {"op": "elements.update", "page_id": page_id,
                         "updates": [{"element_id": box["element_id"], "set": {}}]})


def test_locked_elements_do_not_move_until_unlocked():
    box = {**shape(), "locked": True}
    document = v3(page(box))
    page_id = str(document.pages[0].page_id)
    moved = {"x": 10, "y": 10, "w": 50, "h": 50}
    for change in (
        {"op": "elements.arrange", "page_id": page_id,
         "placements": [{"element_id": box["element_id"], "geometry": moved}]},
        {"op": "elements.update", "page_id": page_id,
         "updates": [{"element_id": box["element_id"], "set": {"geometry": moved}}]},
    ):
        with pytest.raises(ValueError, match="Unlock"):
            apply(document, change)
    unlocked = apply(document, {"op": "elements.update", "page_id": page_id, "updates": [
        {"element_id": box["element_id"], "set": {"locked": False, "geometry": moved}}]})
    assert unlocked.pages[0].elements[0].geometry.x == 10


def test_removing_a_visual_takes_what_depends_on_it():
    chart, other = visual(), visual(x=500)
    note = {"kind": "annotation", "element_id": ident(),
            "geometry": {"x": 0, "y": 320, "w": 100, "h": 40},
            "target": {"element_id": chart["element_id"], "rule": "max"}}
    arrow = {"kind": "connector", "element_id": ident(),
             "geometry": {"x": 0, "y": 300, "w": 100, "h": 20},
             "start": {"kind": "element", "element_id": note["element_id"]},
             "end": {"kind": "element", "element_id": other["element_id"]}}
    field = {"dataset_id": ident(), "dataset_revision": 1, "field_id": "region"}
    document = v3(
        page(chart, other, note, arrow,
             tab_order=[chart["element_id"], other["element_id"]],
             mobile={"order": [chart["element_id"]], "hidden": [note["element_id"]]}),
        actions=[{"action_id": ident(), "kind": "filter", "source_widget_id": chart["element_id"],
                  "target_widget_ids": [other["element_id"]],
                  "mappings": [{"source": field, "target": field}]}])
    page_id = str(document.pages[0].page_id)
    after = apply(document, {"op": "elements.remove", "page_id": page_id,
                             "element_ids": [chart["element_id"]]})
    # The annotation on the chart goes, and so does the arrow from that annotation.
    assert [str(e.element_id) for e in after.pages[0].elements] == [other["element_id"]]
    assert [str(i) for i in after.pages[0].tab_order] == [other["element_id"]]
    assert after.pages[0].mobile.order == () and after.pages[0].mobile.hidden == ()
    assert after.actions == ()


def test_arrange_moves_any_subset_and_reorder_needs_every_element():
    a, b = shape(), shape(x=100)
    document = v3(page(a, b))
    page_id = str(document.pages[0].page_id)
    arranged = apply(document, {"op": "elements.arrange", "page_id": page_id, "placements": [
        {"element_id": b["element_id"], "geometry": {"x": 0, "y": 0, "w": 50, "h": 50}}]})
    assert [e.geometry.x for e in arranged.pages[0].elements] == [0, 0]  # overlap is fine
    reordered = apply(document, {"op": "elements.reorder", "page_id": page_id,
                                 "element_ids": [b["element_id"], a["element_id"]]})
    assert [str(e.element_id) for e in reordered.pages[0].elements] == [
        b["element_id"], a["element_id"]]
    with pytest.raises(ValueError, match="exactly once"):
        apply(document, {"op": "elements.reorder", "page_id": page_id,
                         "element_ids": [a["element_id"]]})


def test_page_canvas_tab_order_and_mobile_ops():
    a, b = shape(), shape(x=100)
    document = v3(page(a, b))
    page_id = str(document.pages[0].page_id)
    after = apply(document,
                  {"op": "page.canvas.set", "page_id": page_id, "canvas": {
                      "size": "4:3", "width": 1024, "height": 768,
                      "background": {"kind": "gradient", "start": "background", "end": "muted"},
                      "grid": {"columns": 8, "show": False, "snap": False}}},
                  {"op": "page.tab_order.set", "page_id": page_id,
                   "element_ids": [b["element_id"], a["element_id"]]},
                  {"op": "page.mobile.set", "page_id": page_id,
                   "mobile": {"hidden": [a["element_id"]]}})
    canvas = after.pages[0].canvas
    assert (canvas.size, canvas.grid.columns, canvas.grid.show, canvas.grid.snap) == (
        "4:3", 8, False, False)
    assert [e.element_id for e in tab_order(after.pages[0])] == [UUID(b["element_id"]),
                                                                UUID(a["element_id"])]


def test_legacy_grid_ops_are_adapted_onto_the_canvas():
    payload = load(FIXTURES / "multi-page-interactions.json")
    document = dashboard_definition(payload)
    summary = payload["pages"][0]
    left, right = (w["widget_id"] for w in summary["widgets"])
    # Unchanged cells keep their exact geometry; a moved cell snaps to the grid.
    nudged = apply(document, {"op": "elements.arrange", "page_id": summary["page_id"],
                              "placements": [{"element_id": right, "geometry": {
                                  "x": 611, "y": 9, "w": 580, "h": 280}}]})
    swapped = apply(nudged, {"op": "layout.apply", "page_id": summary["page_id"], "placements": [
        {"widget_id": left, "layout": {"x": 0, "y": 4, "w": 6, "h": 4}},
        {"widget_id": right, "layout": {"x": 6, "y": 0, "w": 6, "h": 4}}]})
    boxes = {str(e.element_id): e.geometry for e in swapped.pages[0].elements}
    assert (boxes[left].x, boxes[left].y) == (6, 294)
    assert (boxes[right].x, boxes[right].y, boxes[right].w) == (611, 9, 580)
    with pytest.raises(ValueError, match="no spacers"):
        apply(document, {"op": "widget.add", "page_id": summary["page_id"], "widget": {
            "kind": "spacer", "widget_id": ident(), "layout": {"x": 0, "y": 9, "w": 2, "h": 2}}})


def test_a_scrolling_page_grows_for_what_is_placed_below_it():
    document = dashboard_definition(load(FIXTURES / "minimal-empty-page.json"))
    page_id = str(document.pages[0].page_id)
    grown = apply(document, {"op": "widget.add", "page_id": page_id, "widget": {
        "kind": "text", "widget_id": ident(), "text": "Footer",
        "layout": {"x": 0, "y": 20, "w": 12, "h": 2}}})
    assert grown.pages[0].canvas.height == 22 * 72
    fixed = v3(page(shape()))
    with pytest.raises(ValidationError, match="overhang"):
        apply(fixed, {"op": "elements.arrange", "page_id": str(fixed.pages[0].page_id),
                      "placements": [{"element_id": str(fixed.pages[0].elements[0].element_id),
                                      "geometry": {"x": 0, "y": 900, "w": 50, "h": 50}}]})


def test_a_legacy_replace_keeps_bound_text_it_did_not_change():
    chart = visual()
    note = {"kind": "text", "element_id": ident(), "role": "heading",
            "geometry": {"x": 6, "y": 438, "w": 588, "h": 132},
            "text": {"runs": [{"kind": "text", "text": "Peak "},
                              {"kind": "value", "value": {"element_id": chart["element_id"],
                                                          "measure_id": "revenue",
                                                          "rule": "max"}}]}}
    document = v3(page(chart, note))
    page_id = str(document.pages[0].page_id)
    grid_view = legacy_widgets(document.pages[0])[1]
    assert grid_view.text == "Peak "  # bound numbers are never written as literals
    renamed = apply(document, {"op": "widget.replace", "page_id": page_id, "widget": {
        **grid_view.model_dump(mode="json"), "title": "Highlight"}})
    element = renamed.pages[0].elements[1]
    assert element.title == "Highlight" and element.role == "heading"
    assert element.text == document.pages[0].elements[1].text


def test_reading_order_is_rows_top_to_bottom_then_left_to_right():
    left, right, below = visual(x=0, y=10), visual(x=500, y=0), visual(x=0, y=400)
    built = v3(page(below, right, left)).pages[0]
    assert [str(e.element_id) for e in reading_order(built.elements)] == [
        left["element_id"], right["element_id"], below["element_id"]]


def test_grouping_keeps_positions_stack_and_interactions():
    back, chart, middle, front = shape(), visual(x=100, y=40), visual(x=600, y=300), shape(x=10)
    field = {"dataset_id": ident(), "dataset_revision": 1, "field_id": "region"}
    action = {"action_id": ident(), "kind": "filter", "source_widget_id": chart["element_id"],
              "target_widget_ids": [middle["element_id"]],
              "mappings": [{"source": field, "target": field}]}
    document = v3(page(back, chart, middle, front,
                       tab_order=[chart["element_id"], middle["element_id"]],
                       mobile={"order": [front["element_id"], chart["element_id"],
                                         middle["element_id"]],
                               "hidden": [chart["element_id"], middle["element_id"]]}),
                  actions=[action])
    page_id, group_id = str(document.pages[0].page_id), ident()
    grouped = apply(document, {"op": "elements.group", "page_id": page_id, "group_id": group_id,
                               "element_ids": [middle["element_id"], chart["element_id"]],
                               "name": "Sales"})
    top = grouped.pages[0].elements
    # Stacked where the topmost member was, members keep their own order.
    assert [str(item.element_id) for item in top] == [back["element_id"], group_id,
                                                      front["element_id"]]
    group = top[1]
    assert (group.name, group.geometry.x, group.geometry.y, group.geometry.w,
            group.geometry.h) == ("Sales", 100, 40, 900, 560)
    assert [(str(c.element_id), c.geometry.x, c.geometry.y) for c in group.children] == [
        (chart["element_id"], 0, 0), (middle["element_id"], 500, 260)]
    assert grouped.actions == document.actions
    assert [str(i) for i in grouped.pages[0].tab_order] == [chart["element_id"],
                                                            middle["element_id"]]
    assert [str(i) for i in grouped.pages[0].mobile.order] == [front["element_id"], group_id]
    assert [str(i) for i in grouped.pages[0].mobile.hidden] == [group_id]

    released = apply(grouped, {"op": "elements.ungroup", "page_id": page_id,
                               "group_id": group_id})
    assert [(str(e.element_id), e.geometry.x, e.geometry.y)
            for e in released.pages[0].elements] == [
        (back["element_id"], 0, 0), (chart["element_id"], 100, 40),
        (middle["element_id"], 600, 300), (front["element_id"], 10, 0)]
    assert [str(i) for i in released.pages[0].mobile.order] == [
        front["element_id"], chart["element_id"], middle["element_id"]]
    assert released.actions == document.actions


def test_grouping_refuses_nested_unknown_or_repeated_members():
    a, b = shape(), shape(x=100)
    document = v3(page(a, b))
    page_id, group_id = str(document.pages[0].page_id), ident()
    grouped = apply(document, {"op": "elements.group", "page_id": page_id, "group_id": group_id,
                               "element_ids": [a["element_id"]]})
    for members, message in (([a["element_id"]], "page itself"),
                             ([b["element_id"], b["element_id"]], "once"),
                             ([ident()], "page itself")):
        with pytest.raises(ValueError, match=message):
            apply(grouped, {"op": "elements.group", "page_id": page_id, "group_id": ident(),
                            "element_ids": members})
    with pytest.raises(ValueError, match="already exists"):
        apply(grouped, {"op": "elements.group", "page_id": page_id, "group_id": b["element_id"],
                        "element_ids": [b["element_id"]]})
    with pytest.raises(ValueError, match="Only a group"):
        apply(grouped, {"op": "elements.ungroup", "page_id": page_id,
                        "group_id": b["element_id"]})


def test_ungrouping_keeps_what_the_group_hid_or_locked():
    a = shape(x=20, y=30)
    group = {"kind": "group", "element_id": ident(), "hidden": True, "locked": True,
             "geometry": {"x": 100, "y": 100, "w": 200, "h": 200}, "children": [a]}
    document = v3(page(group, tab_order=[group["element_id"]]))
    page_id = str(document.pages[0].page_id)
    after = apply(document, {"op": "elements.ungroup", "page_id": page_id,
                             "group_id": group["element_id"]})
    child = after.pages[0].elements[0]
    assert (child.geometry.x, child.geometry.y, child.hidden, child.locked) == (120, 130, True,
                                                                              True)
    assert after.pages[0].tab_order == ()
