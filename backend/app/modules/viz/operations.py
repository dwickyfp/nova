"""Apply an atomic semantic operation before committing a document revision."""

from __future__ import annotations

from typing import Any

from .contracts.commands import (
    ActionsReplace,
    DatasetsReplace,
    FiltersReplace,
    LayoutChange,
    Operation,
    PageAdd,
    PageOrder,
    PageRemove,
    PageRename,
    Rename,
    WidgetAdd,
    WidgetRemove,
    WidgetReplace,
)
from .contracts.dashboards import DashboardDefinition


def apply_operation(document: DashboardDefinition, operation: Operation) -> DashboardDefinition:
    """Validate the complete final layout so a reflow may move neighbors atomically."""
    data = document.model_dump(mode="json")
    pages = data["pages"]
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
            pages.append(change.page.model_dump(mode="json"))
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
                removed.update(widget["widget_id"] for widget in page["widgets"])
            elif isinstance(change, PageRename):
                page["title"] = change.title
            elif isinstance(change, LayoutChange):
                placements = {str(item.widget_id): item for item in change.placements}
                widgets = {widget["widget_id"]: widget for widget in page["widgets"]}
                if len(placements) != len(change.placements) or set(placements) != set(widgets):
                    raise ValueError("Layout must contain every page widget exactly once")
                for widget_id, placement in placements.items():
                    widget = widgets[widget_id]
                    layout = placement.layout.model_dump(mode="json")
                    if widget["locked"] and layout != widget["layout"]:
                        raise ValueError("Unlock the widget before changing its layout")
                    widget["layout"] = layout
            elif isinstance(change, (WidgetAdd, WidgetReplace)):
                index = next((i for i, item in enumerate(page["widgets"])
                              if item["widget_id"] == str(change.widget.widget_id)), None)
                if isinstance(change, WidgetAdd):
                    if index is not None:
                        raise ValueError("Widget already exists")
                    page["widgets"].append(change.widget.model_dump(mode="json"))
                else:
                    if index is None:
                        raise ValueError("Unknown widget")
                    page["widgets"][index] = change.widget.model_dump(mode="json")
            elif isinstance(change, WidgetRemove):
                widget = next((item for item in page["widgets"]
                               if item["widget_id"] == str(change.widget_id)), None)
                if widget is None:
                    raise ValueError("Unknown widget")
                page["widgets"].remove(widget)
                removed.add(widget["widget_id"])
    if removed:
        data["actions"] = _without_widgets(data.get("actions") or [], removed, pages)
    return DashboardDefinition.model_validate(data)


def _without_widgets(
    actions: list[dict[str, Any]], removed: set[str], pages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Interactions after widgets this operation removed: an action from a removed
    chart goes with it, and removed targets leave the actions that named them."""
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
