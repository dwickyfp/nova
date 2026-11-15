"""Apply an atomic semantic operation before committing a document revision."""

from __future__ import annotations

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
    return DashboardDefinition.model_validate(data)
