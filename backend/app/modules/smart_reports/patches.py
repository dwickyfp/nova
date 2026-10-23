"""Typed definition edits; exploration events never enter this module."""

from __future__ import annotations

from typing import Any

from app.modules.smart_reports.schemas import ReportPatch, ReportSpecV1


def apply_patch(spec: ReportSpecV1, operations: list[ReportPatch]) -> ReportSpecV1:
    data = spec.model_dump(mode="json")
    for operation in operations:
        op, target, value = operation.op, operation.target, operation.value
        pages = data["pages"]
        sections = [s for p in pages for s in p["sections"]]
        widgets = [w for s in sections for w in s["widgets"]]
        if op == "SET_REPORT_METADATA":
            if not set(value) <= {"title", "description", "locale", "timezone", "calendar"}:
                raise ValueError("Only report presentation metadata can be edited")
            data.update(value)
        elif op == "SET_SOURCES":
            if set(value) != {"sources"}:
                raise ValueError("Source replacement requires sources")
            data["sources"] = value["sources"]
        elif op == "ADD_PAGE":
            pages.append(value)
        elif op == "REMOVE_PAGE":
            pages.remove(_find(pages, target))
        elif op == "RENAME_PAGE":
            if set(value) != {"title"}:
                raise ValueError("Page rename requires title")
            _find(pages, target).update(value)
        elif op == "MOVE_PAGE":
            if set(value) != {"index"} or type(value["index"]) is not int:
                raise ValueError("Page order requires an integer index")
            if not 0 <= value["index"] < len(pages):
                raise ValueError("Page position is out of range")
            page = _find(pages, target)
            pages.remove(page)
            pages.insert(value["index"], page)
        elif op == "ADD_WIDGET":
            _find(sections, operation.parent)["widgets"].append(value)
        elif op == "REMOVE_WIDGET":
            widget = _find(widgets, target)
            next(s for s in sections if widget in s["widgets"])["widgets"].remove(widget)
        elif op in {"MOVE_WIDGET", "RESIZE_WIDGET"}:
            widget = _find(widgets, target)
            widget["placement"] = value
            if op == "MOVE_WIDGET" and operation.parent:
                next(s for s in sections if widget in s["widgets"])["widgets"].remove(widget)
                _find(sections, operation.parent)["widgets"].append(widget)
        elif op == "SET_WIDGET_VISUAL":
            if not set(value) <= {
                "kind",
                "encoding",
                "title",
                "pivot_columns",
                "drill_dimensions",
                "detail_fields",
            }:
                raise ValueError("Visual edits accept title, kind and encoding only")
            _find(widgets, target).update(value)
        elif op == "SET_WIDGET_QUERY":
            _find(widgets, target).update(query=value, mapping_required=False)
        elif op == "SET_WIDGET_FORMAT":
            widget = _find(widgets, target)
            if "conditional_formats" in value:
                widget["conditional_formats"] = value["conditional_formats"]
                value = {k: v for k, v in value.items() if k != "conditional_formats"}
            if value:
                widget["format"] = value
        elif op in {"ADD_FILTER", "UPDATE_FILTER", "REMOVE_FILTER"}:
            if op == "ADD_FILTER":
                data["filters"].append(value)
            elif op == "REMOVE_FILTER":
                data["filters"].remove(_find(data["filters"], target))
            else:
                if "id" in value and value["id"] != target:
                    raise ValueError("Filter identity cannot be changed")
                _find(data["filters"], target).update(value)
        elif op == "SET_INTERACTION":
            if target:
                data["interactions"].remove(_find(data["interactions"], target))
            data["interactions"].append(value)
        elif op == "REMOVE_INTERACTION":
            data["interactions"].remove(_find(data["interactions"], target))
        else:
            raise ValueError("Restore is resolved from authorized server history")
    return ReportSpecV1.model_validate(data)


def _find(items: list[dict[str, Any]], identity: str | None) -> dict[str, Any]:
    found = next((item for item in items if item["id"] == identity), None)
    if found is None:
        raise ValueError("Report element does not exist")
    return found
