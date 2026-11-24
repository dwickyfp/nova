"""Apply an atomic semantic operation before committing a document revision.

Every result is a schema-3 canvas document. The legacy grid operations
(``layout.apply`` and ``widget.*``) are API compatibility for clients that
still speak 12-column cells: they are converted onto the page's canvas here,
``layout.apply`` as an ``elements.arrange``. Their request shape is unchanged,
so receipts stored before schema 3 still replay with the same digest.
"""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from .canvas import (
    LEGACY_GRID,
    appearance_of,
    cell_of,
    cell_rect,
    upcast_page,
    upcast_spacer,
    upcast_widget,
)
from .contracts.commands import (
    ActionsReplace,
    DatasetsReplace,
    ElementPlacement,
    ElementsAdd,
    ElementsArrange,
    ElementsRemove,
    ElementsReorder,
    ElementsUpdate,
    FiltersReplace,
    LayoutChange,
    Operation,
    PageAdd,
    PageCanvasSet,
    PageMobileSet,
    PageOrder,
    PageRemove,
    PageRename,
    PageTabOrderSet,
    Rename,
    StackAbove,
    WidgetAdd,
    WidgetRemove,
    WidgetReplace,
)
from .contracts.dashboards import CanvasGrid, DashboardDefinition, Element, ElementStyle, Geometry
from .contracts.dashboards_v2 import Layout, PageV2, SpacerWidget, TextWidget


def apply_operation(document: DashboardDefinition, operation: Operation) -> DashboardDefinition:
    """Validate the complete final document so a batch may move neighbours atomically."""
    data = document.model_dump(mode="json")
    pages: list[dict[str, Any]] = data["pages"]
    removed: set[str] = set()
    for change in operation.changes:
        if isinstance(change, Rename):
            data["title"] = change.title
        elif isinstance(change, FiltersReplace):
            data["filters"] = [item.model_dump(mode="json") for item in change.filters]
        elif isinstance(change, ActionsReplace):
            data["actions"] = [item.model_dump(mode="json") for item in change.actions]
        elif isinstance(change, DatasetsReplace):
            data["datasets"] = [item.model_dump(mode="json") for item in change.datasets]
        elif isinstance(change, PageAdd):
            if any(page["page_id"] == str(change.page.page_id) for page in pages):
                raise ValueError("Page already exists")
            added = upcast_page(change.page) if isinstance(change.page, PageV2) else change.page
            pages.append(added.model_dump(mode="json"))
        elif isinstance(change, PageOrder):
            by_id = {page["page_id"]: page for page in pages}
            order = [str(page_id) for page_id in change.page_ids]
            if len(order) != len(by_id) or set(order) != set(by_id):
                raise ValueError("Page order must contain every page exactly once")
            pages[:] = [by_id[page_id] for page_id in order]
        else:
            page = next((item for item in pages if item["page_id"] == str(change.page_id)), None)
            if page is None:
                raise ValueError("Unknown page")
            if isinstance(change, PageRemove):
                pages.remove(page)
                removed.update(_ids(page["elements"]))
            elif isinstance(change, PageRename):
                page["title"] = change.title
            elif isinstance(change, PageCanvasSet):
                page["canvas"] = change.canvas.model_dump(mode="json")
            elif isinstance(change, PageTabOrderSet):
                page["tab_order"] = [str(item) for item in change.element_ids]
            elif isinstance(change, PageMobileSet):
                page["mobile"] = change.mobile.model_dump(mode="json")
            elif isinstance(change, ElementsAdd):
                _add(page, [item.model_dump(mode="json") for item in change.elements], change.stack)
            elif isinstance(change, ElementsUpdate):
                _update(page, change)
            elif isinstance(change, ElementsRemove):
                removed.update(_remove(page, {str(item) for item in change.element_ids}))
            elif isinstance(change, ElementsArrange):
                _arrange(page, change)
            elif isinstance(change, ElementsReorder):
                _reorder(page, [str(item) for item in change.element_ids])
            elif isinstance(change, LayoutChange):
                _legacy_layout(page, change)
            elif isinstance(change, WidgetAdd):
                if isinstance(change.widget, SpacerWidget):
                    raise ValueError("The canvas needs no spacers; leave the space empty")
                grid, width = _grid(page)
                _add(page, [_element_json(change.widget, grid, width)], "front")
            elif isinstance(change, WidgetReplace):
                _legacy_replace(page, change)
            elif isinstance(change, WidgetRemove):
                removed.update(_remove(page, {str(change.widget_id)}))
    if removed:
        data["actions"] = _without_elements(data.get("actions") or [], removed, pages)
    for page in pages:
        _grow(page)
    return DashboardDefinition.model_validate(data)


def _grow(page: dict[str, Any]) -> None:
    """A scrolling (fit-to-width) page grows to hold what was placed below it."""
    canvas = page["canvas"]
    if canvas["fit"] != "width":
        return
    boxes = [item["geometry"] for item in [*page["elements"], *(page.get("legacy_spacers") or [])]]
    bottom = max((box["y"] + box["h"] for box in boxes), default=0)
    canvas["height"] = min(750_000, max(canvas["height"], bottom + canvas["grid"]["margin"]))


# ── Canvas edits ────────────────────────────────────────────────────────

def _ids(elements: list[dict[str, Any]]) -> set[str]:
    found: set[str] = set()
    for element in elements:
        found.add(element["element_id"])
        found.update(_ids(element.get("children") or []))
    return found


def _find(elements: list[dict[str, Any]], identity: str) -> dict[str, Any] | None:
    for element in elements:
        if element["element_id"] == identity:
            return element
        found = _find(element.get("children") or [], identity)
        if found is not None:
            return found
    return None


def _add(page: dict[str, Any], elements: list[dict[str, Any]],
         stack: str | StackAbove) -> None:
    spacers = {item["element_id"] for item in page.get("legacy_spacers") or []}
    taken = _ids(page["elements"]) | spacers
    if taken & _ids(elements) or len(_ids(elements)) != sum(len(_ids([e])) for e in elements):
        raise ValueError("Element already exists")
    stack_list: list[dict[str, Any]] = page["elements"]
    if isinstance(stack, StackAbove):
        index = next((i for i, item in enumerate(stack_list)
                      if item["element_id"] == str(stack.above)), None)
        if index is None:
            raise ValueError("Unknown element to stack above")
        stack_list[index + 1:index + 1] = elements
    elif stack == "back":
        stack_list[:0] = elements
    else:
        stack_list.extend(elements)


def _update(page: dict[str, Any], change: ElementsUpdate) -> None:
    seen: set[str] = set()
    for update in change.updates:
        identity = str(update.element_id)
        if identity in seen:
            raise ValueError("Update each element once per change")
        seen.add(identity)
        element = _find(page["elements"], identity)
        if element is None:
            raise ValueError("Unknown element")
        patch = update.set.model_dump(mode="json", include=update.set.model_fields_set)
        moves = "geometry" in patch and patch["geometry"] != element["geometry"]
        if moves and element["locked"] and patch.get("locked") is not False:
            raise ValueError("Unlock the element before changing its position or size")
        element.update(patch)


def _remove(page: dict[str, Any], targets: set[str]) -> set[str]:
    """Remove elements (or legacy spacers) and everything that hung off them."""
    spacers = page.get("legacy_spacers") or []
    spacer_hits = {item["element_id"] for item in spacers} & targets
    known = _ids(page["elements"]) | spacer_hits
    if not targets <= known:
        raise ValueError("Unknown element")
    page["legacy_spacers"] = [item for item in spacers if item["element_id"] not in targets]
    removed: set[str] = set(spacer_hits)
    pending = set(targets) - spacer_hits
    while pending:
        removed |= pending
        for identity in pending:
            removed |= _ids([_find(page["elements"], identity) or {"element_id": identity}])
        page["elements"] = _prune(page["elements"], removed)
        pending = _dependents(page["elements"], removed) - removed
    page["tab_order"] = [item for item in page.get("tab_order") or [] if item not in removed]
    mobile = page.get("mobile") or {}
    page["mobile"] = {key: [item for item in mobile.get(key) or [] if item not in removed]
                      for key in ("order", "hidden")}
    return removed


def _prune(elements: list[dict[str, Any]], removed: set[str]) -> list[dict[str, Any]]:
    kept = []
    for element in elements:
        if element["element_id"] in removed:
            continue
        if "children" in element:
            element["children"] = _prune(element["children"], removed)
            if not element["children"]:
                continue
        kept.append(element)
    return kept


def _references(element: dict[str, Any]) -> set[str]:
    """Element IDs a connector, annotation or bound value depends on."""
    found: set[str] = set()
    for side in ("start", "end"):
        endpoint = element.get(side) or {}
        if endpoint.get("kind") == "element":
            found.add(endpoint["element_id"])
        elif endpoint.get("kind") == "datum":
            found.add(endpoint["anchor"]["element_id"])
    if element.get("kind") == "annotation":
        found.add(element["target"]["element_id"])
    for run in (element.get("text") or {}).get("runs") or []:
        if run.get("kind") == "value":
            found.add(run["value"]["element_id"])
    return found


def _dependents(elements: list[dict[str, Any]], removed: set[str]) -> set[str]:
    hits: set[str] = set()
    for element in elements:
        if element.get("kind") in {"connector", "annotation"} and _references(element) & removed:
            hits.add(element["element_id"])
        hits |= _dependents(element.get("children") or [], removed)
    return hits


def _arrange(page: dict[str, Any], change: ElementsArrange) -> None:
    placements = {str(item.element_id): item for item in change.placements}
    if len(placements) != len(change.placements):
        raise ValueError("Place each element once per change")
    spacers = {item["element_id"]: item for item in page.get("legacy_spacers") or []}
    for identity, placement in placements.items():
        target = _find(page["elements"], identity) or spacers.get(identity)
        if target is None:
            raise ValueError("Unknown element")
        geometry = placement.geometry.model_dump(mode="json")
        if target["locked"] and geometry != target["geometry"]:
            raise ValueError("Unlock the element before changing its position or size")
        target["geometry"] = geometry


def _reorder(page: dict[str, Any], order: list[str]) -> None:
    by_id = {item["element_id"]: item for item in page["elements"]}
    if len(order) != len(by_id) or set(order) != set(by_id):
        raise ValueError("Element order must list every element on the page exactly once")
    page["elements"] = [by_id[identity] for identity in order]


# ── Legacy grid adapter ─────────────────────────────────────────────────

def _grid(page: dict[str, Any]) -> tuple[CanvasGrid, int]:
    canvas = page.get("canvas") or {}
    return CanvasGrid.model_validate(canvas.get("grid") or LEGACY_GRID.model_dump()), int(
        canvas.get("width", 1280))


def _keep_or_cell(current: dict[str, Any] | None, layout: Layout, grid: CanvasGrid,
                  width: int) -> dict[str, Any]:
    """An unchanged cell keeps the element's exact geometry; a moved one snaps to the cell."""
    if current is not None:
        geometry = Geometry.model_validate(current)
        if cell_of(geometry, grid, width) == layout:
            return current
    return cell_rect(layout, grid, width).model_dump(mode="json")


def _legacy_layout(page: dict[str, Any], change: LayoutChange) -> None:
    grid, width = _grid(page)
    if len({item.widget_id for item in change.placements}) != len(change.placements):
        raise ValueError("Layout must contain every page widget exactly once")
    spacers = {item["element_id"]: item for item in page.get("legacy_spacers") or []}
    placements = []
    for item in change.placements:
        identity = str(item.widget_id)
        target = _find(page["elements"], identity) or spacers.get(identity)
        current = target["geometry"] if target is not None else None
        placements.append(ElementPlacement(
            element_id=item.widget_id,
            geometry=Geometry.model_validate(_keep_or_cell(current, item.layout, grid, width)),
        ))
    _arrange(page, ElementsArrange(page_id=change.page_id, placements=tuple(placements)))


_ELEMENT: TypeAdapter[Element] = TypeAdapter(Element)


def _element_json(widget: Any, grid: CanvasGrid, width: int) -> dict[str, Any]:
    return _ELEMENT.dump_python(_ELEMENT.validate_python(upcast_widget(widget, grid, width)),
                                mode="json")


def _legacy_replace(page: dict[str, Any], change: WidgetReplace) -> None:
    identity = str(change.widget.widget_id)
    grid, width = _grid(page)
    spacers = page.get("legacy_spacers") or []
    for index, spacer in enumerate(spacers):
        if spacer["element_id"] == identity:
            if not isinstance(change.widget, SpacerWidget):
                raise ValueError("A spacer cannot become another kind of widget")
            replaced = upcast_spacer(change.widget, grid, width).model_dump(mode="json")
            replaced["geometry"] = _keep_or_cell(spacer["geometry"], change.widget.layout,
                                                 grid, width)
            spacers[index] = replaced
            return
    if isinstance(change.widget, SpacerWidget):
        raise ValueError("The canvas needs no spacers; leave the space empty")
    elements: list[dict[str, Any]] = page["elements"]
    for index, element in enumerate(elements):
        if element["element_id"] == identity:
            replaced = _element_json(change.widget, grid, width)
            replaced["geometry"] = _keep_or_cell(element["geometry"], change.widget.layout,
                                                 grid, width)
            # Canvas-only properties a grid client cannot express are kept.
            for key in ("name", "hidden", "decorative"):
                replaced[key] = element.get(key, replaced[key])
            old_style = ElementStyle.model_validate(element["style"])
            if appearance_of(old_style) == change.widget.appearance:
                replaced["style"] = element["style"]
            if isinstance(change.widget, TextWidget) and element["kind"] == "text":
                # Unchanged text keeps its runs (bound values), unchanged emphasis its role.
                if _plain(element) == change.widget.text:
                    replaced["text"] = element["text"]
                if (element["role"] == "title") == (change.widget.style == "title"):
                    replaced["role"] = element["role"]
            elements[index] = replaced
            return
    raise ValueError("Unknown widget")


def _plain(element: dict[str, Any]) -> str:
    return "".join(run.get("text", "") for run in element["text"]["runs"]
                   if run.get("kind") == "text")


def _without_elements(
    actions: list[dict[str, Any]], removed: set[str], pages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Interactions after elements this operation removed: an action from a removed
    visual goes with it, and removed targets leave the actions that named them."""
    page_ids = {page["page_id"] for page in pages}
    kept = []
    for action in actions:
        if action["source_widget_id"] in removed:
            continue
        targets = [item for item in action.get("target_widget_ids") or [] if item not in removed]
        if action.get("target_widget_ids") and not targets and action["kind"] != "navigate":
            continue
        if action.get("target_page_id") and action["target_page_id"] not in page_ids:
            continue
        kept.append({**action, "target_widget_ids": targets})
    return kept

